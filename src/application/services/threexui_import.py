"""Importer: 3x-ui (x-ui.db) -> Remnawave panel + our schema.

Unlike the shopbot importer, source clients do NOT exist on the panel yet, so
this one CREATES a panel user per group (panel-first: the panel call happens
before any local rows are staged). The client's VLESS uuid and subId are
preserved (``vlessUuid`` / ``shortUuid`` in the create payload) so existing
device configs and old ``/sub/{subId}`` links keep working after migration.

A failed local commit after panel users were created is healed by re-running:
groups already committed match locally by ``short_id`` and are refreshed
without touching the panel; groups that reached the panel but not the DB come
back as a panel conflict (A019/A020/A021) and land in ``skipped`` for review.

Source quirks (verified against 3x-ui sources): VPN users live as a ``clients``
array inside ``inbounds.settings`` JSON; ``totalGB`` is BYTES despite the name;
``expiryTime`` is epoch milliseconds where 0 = never and NEGATIVE = "start
after first use" countdown duration; ``client_traffics.expiry_time`` is the
live value (materialized after-first-use deadlines) and wins over the JSON.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import uuid as uuid_mod
from typing import TYPE_CHECKING, Any

from src.application.dto.panel import ProvisionSpec
from src.application.services.ids import generate_referral_code, generate_short_id
from src.core.enums import Currency, SubscriptionStatus
from src.core.exceptions import RemnawaveError
from src.core.logging import get_logger
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from pathlib import Path

    from src.application.common.panel import RemnawaveClient
    from src.infrastructure.database.uow import UnitOfWork

log = get_logger(__name__)

# The panel requires expireAt; far-future is the conventional "never".
_NEVER = dt.datetime(2099, 12, 31, tzinfo=dt.UTC)

_BAD_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")

_LOCAL_STATUS: dict[str, SubscriptionStatus] = {
    "DISABLED": SubscriptionStatus.DISABLED,
    "EXPIRED": SubscriptionStatus.EXPIRED,
    "ACTIVE": SubscriptionStatus.ACTIVE,
}


def _to_int(raw: object) -> int | None:
    """tgId is int64 in v2.4+/v3 but a string in v2.2 — parse, don't trust."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _to_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int | float):
        return raw != 0
    return str(raw).strip().lower() in {"1", "t", "true", "yes"}


def _to_json(raw: object) -> Any:
    if isinstance(raw, dict | list):
        return raw
    if raw is None:
        return None
    try:
        return json.loads(str(raw))
    except ValueError:
        return None


def _is_uuid(raw: str) -> bool:
    try:
        uuid_mod.UUID(raw)
    except ValueError:
        return False
    return True


def _normalize_client(
    raw: dict[str, Any], protocol: str, remark: str, traffic_by_email: dict[str, sqlite3.Row]
) -> dict[str, Any]:
    email = str(raw.get("email") or "")
    expiry_ms = _to_int(raw.get("expiryTime")) or 0
    used = 0
    traffic = traffic_by_email.get(email)
    if traffic is not None:
        # The live value: after-first-use deadlines get materialized here.
        live_expiry = _to_int(traffic["expiry_time"])
        if live_expiry is not None:
            expiry_ms = live_expiry
        used = (_to_int(traffic["up"]) or 0) + (_to_int(traffic["down"]) or 0)
    return {
        "email": email,
        "uuid": str(raw.get("id") or ""),
        "password": str(raw.get("password") or ""),
        "protocol": protocol,
        "inbound_remark": remark,
        "tg_id": _to_int(raw.get("tgId")) or None,  # 0 / "" / garbage -> no telegram
        "sub_id": str(raw.get("subId") or ""),
        "expiry_ms": expiry_ms,
        "total_bytes": _to_int(raw.get("totalGB")) or 0,  # bytes despite the name
        "device_limit": _to_int(raw.get("limitIp")) or 0,  # per-client IP/device cap; 0 = unlimited
        "used_bytes": used,
        "enabled": _to_bool(raw.get("enable")),
        "comment": str(raw.get("comment") or ""),
    }


def _group(clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One group == one panel user == one subscription (clients sharing a subId)."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for client in clients:
        buckets.setdefault(str(client["sub_id"] or client["email"]), []).append(client)

    groups: list[dict[str, Any]] = []
    for key, members in buckets.items():
        primary = next(
            (c for c in members if c["protocol"] == "vless"),
            next((c for c in members if c["protocol"] == "vmess"), members[0]),
        )
        expiries = [int(c["expiry_ms"]) for c in members]
        totals = [int(c["total_bytes"]) for c in members]
        limits = [int(c["device_limit"]) for c in members]
        groups.append(
            {
                "key": key,
                "clients": members,
                "primary": primary,
                "tg_id": next((c["tg_id"] for c in members if c["tg_id"] is not None), None),
                # 0 = never / unlimited — it dominates any concrete deadline or cap.
                "expiry_ms": 0 if 0 in expiries else max(expiries),
                "total_bytes": 0 if 0 in totals else max(totals),
                "device_limit": 0 if 0 in limits else max(limits),
                "used_bytes": sum(int(c["used_bytes"]) for c in members),
                "enabled": any(bool(c["enabled"]) for c in members),
            }
        )
    return groups


def _rows(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(query).fetchall()
    except sqlite3.Error:
        return []


def read_source(path: Path) -> dict[str, Any]:
    """Read x-ui.db into normalized client/group dicts (sync — call via asyncio.to_thread).

    Reads BOTH client models: the classic v2 ``inbounds.settings`` JSON and the v3
    normalized ``clients`` + ``client_inbounds`` tables (3x-ui >= 3.x keeps clients
    there and the settings JSON of migrated inbounds may be empty). Deduped by email.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        inbounds = _rows(conn, "SELECT id, remark, protocol, settings FROM inbounds")
        traffics = _rows(
            conn, "SELECT email, up, down, total, expiry_time, enable FROM client_traffics"
        )
        v3_clients = _rows(
            conn,
            "SELECT id, email, sub_id, uuid, password, total_gb, expiry_time,"
            " enable, tg_id, comment FROM clients",
        )
        v3_links = _rows(conn, "SELECT client_id, inbound_id FROM client_inbounds")
    finally:
        conn.close()

    traffic_by_email: dict[str, sqlite3.Row] = {}
    for row in traffics:
        email = str(row["email"] or "")
        if email:
            traffic_by_email[email] = row

    clients: list[dict[str, Any]] = []
    for inbound in inbounds:
        settings = _to_json(inbound["settings"])
        raw_clients = settings.get("clients") if isinstance(settings, dict) else None
        if not isinstance(raw_clients, list):
            continue
        protocol, remark = str(inbound["protocol"] or ""), str(inbound["remark"] or "")
        clients.extend(
            _normalize_client(raw, protocol, remark, traffic_by_email)
            for raw in raw_clients
            if isinstance(raw, dict)
        )

    seen_emails = {c["email"] for c in clients if c["email"]}
    inbound_by_id = {int(row["id"]): row for row in inbounds}
    link_by_client: dict[int, int] = {}
    for row in v3_links:
        link_by_client.setdefault(_to_int(row["client_id"]) or 0, _to_int(row["inbound_id"]) or 0)
    for row in v3_clients:
        email = str(row["email"] or "")
        if email and email in seen_emails:
            continue  # the settings JSON copy already covered it
        linked = inbound_by_id.get(link_by_client.get(_to_int(row["id"]) or 0, -1))
        raw = {
            "email": email,
            "id": row["uuid"],
            "password": row["password"],
            "tgId": row["tg_id"],
            "subId": row["sub_id"],
            "expiryTime": row["expiry_time"],
            "totalGB": row["total_gb"],  # bytes despite the name, same as the JSON key
            "enable": row["enable"],
            "comment": row["comment"],
        }
        protocol = str(linked["protocol"] or "") if linked is not None else ""
        remark = str(linked["remark"] or "") if linked is not None else ""
        clients.append(_normalize_client(raw, protocol, remark, traffic_by_email))

    return {"inbounds": len(inbounds), "clients": clients, "groups": _group(clients)}


def probe(data: dict[str, Any]) -> dict[str, Any]:
    """Counts + sanity check without writing anything (feed it read_source output)."""
    clients: list[dict[str, Any]] = data["clients"]
    groups: list[dict[str, Any]] = data["groups"]
    if not clients:
        return {"ok": False, "detail": "клиенты не найдены — это не x-ui.db или inbounds пусты"}
    now_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    return {
        "ok": True,
        "counts": {
            "inbounds": int(data.get("inbounds") or 0),
            "clients": len(clients),
            "groups": len(groups),
            "with_telegram": sum(1 for g in groups if g["tg_id"] is not None),
            "active": sum(
                1 for g in groups if g["enabled"] and not _is_expired(int(g["expiry_ms"]), now_ms)
            ),
        },
    }


def _is_expired(expiry_ms: int, now_ms: int) -> bool:
    return 0 < expiry_ms <= now_ms


def _panel_status(enabled: bool, expiry_ms: int, now_ms: int) -> str:
    if not enabled:
        return "DISABLED"
    if _is_expired(expiry_ms, now_ms):
        return "EXPIRED"
    return "ACTIVE"


def _expire_at(expiry_ms: int, now: dt.datetime) -> dt.datetime:
    if expiry_ms > 0:
        return dt.datetime.fromtimestamp(expiry_ms / 1000, tz=dt.UTC)
    if expiry_ms == 0:
        return _NEVER
    # "Start after first use" countdown — the clock starts at migration.
    return now + dt.timedelta(milliseconds=-expiry_ms)


def _short_id(sub_id: str) -> str:
    return _BAD_ID_CHARS.sub("", sub_id)[:16] or generate_short_id()


def _username(email: str, taken: set[str]) -> str:
    """Panel usernames: [a-zA-Z0-9_-], 3..36 chars, unique — dedupe within the run."""
    base = _BAD_ID_CHARS.sub("_", email)[:36].ljust(3, "_")
    name, n = base, 1
    while name in taken:
        n += 1
        suffix = f"-{n}"
        name = base[: 36 - len(suffix)] + suffix
    taken.add(name)
    return name


def _build_spec(
    group: dict[str, Any],
    *,
    short_id: str,
    username: str,
    expire_at: dt.datetime,
    status: str,
    squad_uuid: str | None,
) -> ProvisionSpec:
    primary: dict[str, Any] = group["primary"]
    extra: dict[str, object] = {"tag": "XUI_IMPORT", "status": status}
    if primary["sub_id"]:
        # Old /sub/{subId} links survive: the panel accepts a caller-supplied shortUuid.
        extra["shortUuid"] = primary["sub_id"]
    if _is_uuid(str(primary["uuid"])):
        extra["vlessUuid"] = primary["uuid"]  # existing device configs keep working
    password = str(primary["password"])
    if primary["protocol"] in ("trojan", "shadowsocks") and 8 <= len(password) <= 32:
        extra["trojanPassword" if primary["protocol"] == "trojan" else "ssPassword"] = password

    description = f"3x-ui: {primary['email']}"
    if primary["inbound_remark"]:
        description += f" / {primary['inbound_remark']}"
    if int(group["expiry_ms"]) < 0:
        description += " / отсчёт срока начался при миграции"

    return ProvisionSpec(
        short_id=short_id,
        telegram_id=group["tg_id"],
        username=username,
        expire_at=expire_at,
        traffic_limit_bytes=int(group["total_bytes"]),
        device_limit=int(group["device_limit"]),  # 0 = unlimited; preserve the source cap
        internal_squads=(squad_uuid,) if squad_uuid else (),
        description=description,
        extra=extra,
    )


class ThreexuiImportService:
    def __init__(self, panel: RemnawaveClient) -> None:
        self._panel = panel

    async def run(
        self, uow: UnitOfWork, data: dict[str, Any], *, squad_uuid: str | None = None
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "users_created": 0,
            "users_updated": 0,
            "subscriptions": 0,
            "panel_users_created": 0,
            "without_telegram": 0,
            "skipped": [],
        }
        now = dt.datetime.now(dt.UTC)
        now_ms = int(now.timestamp() * 1000)
        taken_names: set[str] = set()
        best_sub: dict[int, Subscription] = {}
        groups: list[dict[str, Any]] = data["groups"]

        for group in groups:
            primary: dict[str, Any] = group["primary"]
            email = str(primary["email"])
            expiry_ms = int(group["expiry_ms"])
            expire_at = _expire_at(expiry_ms, now)
            panel_status = _panel_status(bool(group["enabled"]), expiry_ms, now_ms)
            status = _LOCAL_STATUS[panel_status]
            # Same stable key _group bucketed by (sub_id or email): subId-less clients get a
            # deterministic short_id so a re-run matches the existing sub, not re-provisions.
            short_id = _short_id(str(group["key"]))

            # Idempotency: an already-imported group is refreshed, never re-provisioned.
            existing = await uow.subscriptions.find_one(short_id=short_id)
            if existing is not None:
                existing.status = status
                existing.expire_at = expire_at
                existing.traffic_limit_bytes = int(group["total_bytes"])
                existing.traffic_used_bytes = int(group["used_bytes"])
                summary["users_updated"] += 1
                continue

            spec = _build_spec(
                group,
                short_id=short_id,
                username=_username(email, taken_names),
                expire_at=expire_at,
                status=panel_status,
                squad_uuid=squad_uuid,
            )
            try:
                panel_user = await self._panel.create_user(spec)
            except RemnawaveError as exc:
                log.warning("threexui_import panel rejected", email=email, error=str(exc))
                summary["skipped"].append(f"{email}: панель отклонила ({exc})")
                continue
            summary["panel_users_created"] += 1

            tg_id: int | None = group["tg_id"]
            user = await uow.users.find_one(telegram_id=tg_id) if tg_id else None
            if user is None:
                user = User(
                    telegram_id=tg_id,
                    username=email[:64] or None,
                    referral_code=generate_referral_code(),
                    currency=Currency.RUB,
                    is_trial_available=False,
                )
                await uow.users.add(user)
                summary["users_created"] += 1
            else:
                summary["users_updated"] += 1
            if tg_id is None:
                summary["without_telegram"] += 1

            sub = Subscription(
                user_id=user.id,
                remnawave_uuid=panel_user.uuid,
                short_id=short_id,
                status=status,
                expire_at=expire_at,
                traffic_limit_bytes=int(group["total_bytes"]),
                device_limit=int(group["device_limit"]),
                traffic_used_bytes=int(group["used_bytes"]),
                internal_squads=[squad_uuid] if squad_uuid else [],
                subscription_url=panel_user.subscription_url,
                plan_snapshot={
                    "name": primary["inbound_remark"] or "3x-ui",
                    "source": "3x-ui",
                    "email": email,
                    "protocol": primary["protocol"],
                },
            )
            await uow.subscriptions.add(sub)
            summary["subscriptions"] += 1

            if status.is_usable:
                current = best_sub.get(user.id)
                if current is None or (sub.expire_at or now) > (current.expire_at or now):
                    best_sub[user.id] = sub

        await uow.session.flush()
        for user_id, sub in best_sub.items():
            owner = await uow.users.get(user_id)
            if owner is not None and owner.current_subscription_id is None:
                owner.current_subscription_id = sub.id
        return summary
