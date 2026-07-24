"""Importer: SoloBot (Vladless/Solo_bot, Python, PostgreSQL).

SoloBot keys the customer on ``users.tg_id`` and stores each VPN subscription as a row in
``keys`` (a user can have several). A key carries ``client_id`` (the panel user UUID for a
Remnawave install), ``remnawave_link`` (the subscription URL) and ``expiry_time`` (epoch —
milliseconds like its 3x-ui heritage, tolerated in seconds too). Balance is FLOAT RUBLES on
the user; a paid payment has ``status == "success"``; referrals join on TELEGRAM ids.

Panel uuid: adopted from ``keys.client_id`` when it's a real UUID (the operator points the new
bot at the SAME Remnawave), else resolved from the live panel by telegram_id, else left empty
(a record — the next renewal re-provisions). Idempotent: users by telegram_id, subscriptions by
remnawave_uuid (when known) or short_id, transactions by external_id, promocodes by code.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid as uuid_mod
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from src.application.services.ids import generate_referral_code, generate_short_id
from src.application.services.pgdump import looks_like_pgdump, parse_copy_blocks
from src.core.enums import (
    Availability,
    Currency,
    Locale,
    PaymentGatewayType,
    RewardType,
    SubscriptionStatus,
    TransactionStatus,
    TransactionType,
)
from src.infrastructure.database.models.promocode import Promocode
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from pathlib import Path

    from src.application.common.panel import RemnawaveClient
    from src.application.services.referral import ReferralService
    from src.infrastructure.database.uow import UnitOfWork

SOURCE_TABLES: frozenset[str] = frozenset(
    {"users", "keys", "payments", "referrals", "coupons", "tariffs"}
)

# SoloBot ``payments.payment_system`` -> our gateway. Unknown values keep the raw name as label.
_GATEWAYS = {
    "yookassa": PaymentGatewayType.YOOKASSA,
    "yoomoney": PaymentGatewayType.YOOMONEY,
    "cryptobot": PaymentGatewayType.CRYPTOBOT,
    "crypto": PaymentGatewayType.CRYPTOBOT,
    "cryptomus": PaymentGatewayType.CRYPTOMUS,
    "heleket": PaymentGatewayType.HELEKET,
    "stars": PaymentGatewayType.TELEGRAM_STARS,
    "telegram": PaymentGatewayType.TELEGRAM_STARS,
    "robokassa": PaymentGatewayType.ROBOKASSA,
    "freekassa": PaymentGatewayType.FREEKASSA,
    "wata": PaymentGatewayType.WATA,
    "kassai": PaymentGatewayType.KASSA_AI,
    "lava": PaymentGatewayType.LAVA,
    "tribute": PaymentGatewayType.TRIBUTE,
    "platega": PaymentGatewayType.PLATEGA,
}


def _to_int(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None if raw is None else int(raw)
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _to_float(raw: object) -> float | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_utc(raw: object) -> dt.datetime | None:
    if raw is None or raw == "":
        return None
    value = raw if isinstance(raw, dt.datetime) else None
    if value is None:
        try:
            value = dt.datetime.fromisoformat(str(raw).strip())
        except ValueError:
            return None
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _epoch_to_utc(raw: object) -> dt.datetime | None:
    """SoloBot ``expiry_time`` is an epoch integer — milliseconds (3x-ui heritage), but tolerate
    seconds too: anything past ~year 33658 in seconds is really milliseconds."""
    n = _to_int(raw)
    if n is None or n <= 0:
        return None
    secs = n / 1000 if n > 10**12 else float(n)
    try:
        return dt.datetime.fromtimestamp(secs, tz=dt.UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _short_id_from_url(raw: object) -> str | None:
    url = str(raw or "")
    if not url:
        return None
    seg = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1][:16]
    if seg and seg.isascii() and all(c.isalnum() or c in "_-" for c in seg):
        return seg
    return None


def _as_uuid(raw: object) -> uuid_mod.UUID | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return uuid_mod.UUID(s)
    except ValueError:
        return None


def read_source(path: Path) -> dict[str, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        loaded: Any = json.loads(stripped)
        if not isinstance(loaded, dict):
            raise ValueError("это не дамп SoloBot")
        parsed = {
            str(table): [row for row in rows if isinstance(row, dict)]
            for table, rows in loaded.items()
            if isinstance(rows, list)
        }
    elif looks_like_pgdump(text):
        parsed = dict(parse_copy_blocks(text, set(SOURCE_TABLES)))
    else:
        raise ValueError("это не .sql дамп SoloBot")
    return {table: parsed.get(table, []) for table in SOURCE_TABLES}


async def read_source_dsn(dsn: str) -> dict[str, list[dict[str, Any]]]:
    import asyncpg  # type: ignore[import-untyped]

    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        data: dict[str, list[dict[str, Any]]] = {}
        for table in sorted(SOURCE_TABLES):
            try:
                records = await conn.fetch(f'SELECT * FROM "{table}"')
            except asyncpg.PostgresError:
                data[table] = []
            else:
                data[table] = [dict(r) for r in records]
        return data
    finally:
        await conn.close()


def probe(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    if not data.get("users"):
        return {"ok": False, "detail": "таблица users пуста или это не дамп SoloBot"}
    # A SoloBot key row is unmistakable — it carries client_id + expiry_time.
    keys = data.get("keys", [])
    if keys and not any("client_id" in k and "expiry_time" in k for k in keys):
        return {"ok": False, "detail": "структура keys не похожа на SoloBot"}
    paid = [p for p in data.get("payments", []) if str(p.get("status")).lower() == "success"]
    return {
        "ok": True,
        "counts": {
            "users": len(data["users"]),
            "keys": len(keys),
            "paid_payments": len(paid),
            "referrals": len(data.get("referrals", [])),
            "coupons": len(data.get("coupons", [])),
        },
    }


class SolobotImportService:
    def __init__(self, referrals: ReferralService, panel: RemnawaveClient | None = None) -> None:
        self._referrals = referrals
        self._panel = panel

    async def run(self, uow: UnitOfWork, data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "users_created": 0,
            "users_updated": 0,
            "referrals_linked": 0,
            "subscriptions": 0,
            "transactions": 0,
            "promocodes": 0,
            "skipped": [],
        }
        tariff_names = {
            _to_int(t.get("id")): str(t.get("name") or "").strip()
            for t in data.get("tariffs", [])
            if _to_int(t.get("id")) is not None
        }
        by_tid = await self._import_users(uow, data["users"], summary)
        await self._import_keys(uow, data["keys"], by_tid, tariff_names, summary)
        await self._link_referrals(uow, data["referrals"], by_tid, summary)
        await self._import_payments(uow, data["payments"], by_tid, summary)
        await self._import_coupons(uow, data.get("coupons", []), summary)
        return summary

    async def _import_users(
        self, uow: UnitOfWork, rows: list[dict[str, Any]], summary: dict[str, Any]
    ) -> dict[int, User]:
        by_tid: dict[int, User] = {}
        for row in rows:
            tid = _to_int(row.get("tg_id"))
            if tid is None:
                continue
            user = await uow.users.find_one(telegram_id=tid)
            if user is None:
                user = User(
                    telegram_id=tid, referral_code=generate_referral_code(), currency=Currency.RUB
                )
                await uow.users.add(user)
                created = _to_utc(row.get("created_at"))
                if created is not None:
                    user.created_at = created
                summary["users_created"] += 1
            else:
                summary["users_updated"] += 1
            user.first_name = (str(row.get("first_name") or "") or None) and str(
                row.get("first_name")
            )[:128]
            user.username = (str(row.get("username") or "") or None) and str(row.get("username"))[
                :64
            ]
            language = str(row.get("language_code") or "").strip().lower()[:2]
            user.language = Locale(language) if language in set(Locale) else Locale.default()
            # balance is FLOAT rubles -> minor units (kopeks). Only raise it (never wipe a value
            # that already exists on a re-run against a partially-migrated bot).
            balance = _to_float(row.get("balance"))
            if balance is not None:
                user.balance_minor = max(user.balance_minor, round(balance * 100))
            by_tid[tid] = user
            await uow.session.flush()
        return by_tid

    async def _resolve_uuid(self, telegram_id: int) -> uuid_mod.UUID | None:
        if self._panel is None:
            return None
        try:
            panel_user = await self._panel.get_user_by_telegram_id(telegram_id)
        except Exception:
            return None
        return getattr(panel_user, "uuid", None)

    async def _import_keys(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_tid: dict[int, User],
        tariff_names: dict[int | None, str],
        summary: dict[str, Any],
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        for row in rows:
            user = by_tid.get(_to_int(row.get("tg_id")) or 0)
            if user is None:
                continue
            expire = _epoch_to_utc(row.get("expiry_time"))
            link = str(row.get("remnawave_link") or row.get("key") or "")
            if expire is None and not link:
                continue
            # Adopt the panel uuid from client_id (a real UUID on Remnawave); else resolve live.
            panel_uuid = _as_uuid(row.get("client_id")) or await self._resolve_uuid(
                user.telegram_id or 0
            )
            sub = (
                await uow.subscriptions.find_one(remnawave_uuid=panel_uuid)
                if panel_uuid is not None
                else None
            )
            if sub is None:
                short = _short_id_from_url(link)
                if short is None or await uow.subscriptions.find_one(short_id=short) is not None:
                    short = generate_short_id()
                sub = Subscription(user_id=user.id, remnawave_uuid=panel_uuid, short_id=short)
                await uow.subscriptions.add(sub)
            sub.expire_at = expire
            sub.subscription_url = link[:512] or None
            sub.device_limit = _to_int(row.get("selected_device_limit"))
            traffic = _to_int(row.get("selected_traffic_limit"))
            sub.traffic_limit_bytes = traffic if traffic and traffic > 0 else 0
            if bool(row.get("is_frozen")):
                sub.status = SubscriptionStatus.DISABLED
            elif expire is not None and expire > now:
                sub.status = SubscriptionStatus.ACTIVE
            else:
                sub.status = SubscriptionStatus.EXPIRED
            tariff_name = (
                tariff_names.get(_to_int(row.get("tariff_id")))
                or str(row.get("alias") or "").strip()
            )
            sub.plan_snapshot = {"name": tariff_name or "Imported", "source": "solobot"}
            await uow.session.flush()
            if sub.status.is_usable and user.current_subscription_id is None:
                user.current_subscription_id = sub.id
            summary["subscriptions"] += 1
        await uow.session.flush()

    async def _link_referrals(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_tid: dict[int, User],
        summary: dict[str, Any],
    ) -> None:
        """referrer_tg_id / referred_tg_id are TELEGRAM ids."""
        for row in rows:
            referred = by_tid.get(_to_int(row.get("referred_tg_id")) or 0)
            referrer = by_tid.get(_to_int(row.get("referrer_tg_id")) or 0)
            if referred is None or referrer is None or referred.referred_by_id is not None:
                continue
            if await self._referrals.bind(uow, referred, referrer.referral_code) is not None:
                summary["referrals_linked"] += 1

    async def _import_payments(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_tid: dict[int, User],
        summary: dict[str, Any],
    ) -> None:
        for row in rows:
            if str(row.get("status") or "").strip().lower() != "success":
                continue
            user = by_tid.get(_to_int(row.get("tg_id")) or 0)
            if user is None:
                continue
            pid = str(row.get("payment_id") or "").strip() or _to_int(row.get("id"))
            external = f"solobot-{pid}"
            if await uow.transactions.find_one(external_id=external) is not None:
                continue
            amount = _to_float(row.get("amount"))
            if amount is None or amount <= 0:
                continue
            cur_name = str(row.get("currency") or "RUB").strip().upper()
            currency = Currency[cur_name] if cur_name in Currency.__members__ else Currency.RUB
            amount_minor = int(
                (Decimal(str(amount)) * 10**currency.exponent).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            system = str(row.get("payment_system") or "").strip().lower()
            created = _to_utc(row.get("created_at")) or dt.datetime.now(dt.UTC)
            txn = Transaction(
                user_id=user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                status=TransactionStatus.COMPLETED,
                amount_minor=amount_minor,
                currency=currency,
                external_id=external,
                gateway_type=_GATEWAYS.get(system),
                gateway_display_name=(system or None) and system[:64],
                completed_at=created,
            )
            await uow.transactions.add(txn)
            txn.created_at = created
            summary["transactions"] += 1

    async def _import_coupons(
        self, uow: UnitOfWork, rows: list[dict[str, Any]], summary: dict[str, Any]
    ) -> None:
        """SoloBot coupons are balance codes (``amount`` rubles). Percent-only coupons have no
        1:1 mapping to our reward types, so they're skipped with a note."""
        for row in rows:
            code = str(row.get("code") or "").strip()[:64]
            if not code or await uow.promocodes.find_one(code=code) is not None:
                continue
            amount = _to_int(row.get("amount")) or 0
            percent = _to_int(row.get("percent")) or 0
            if amount <= 0:
                if percent > 0:
                    summary["skipped"].append(f"купон {code}: скидка %-ом не переносится")
                continue
            limit = _to_int(row.get("usage_limit"))
            promo = Promocode(
                code=code,
                reward_type=RewardType.BALANCE,
                reward_value=amount * 100,  # rubles -> kopeks
                availability=Availability.ALL,
                max_activations=limit if limit and limit > 0 else None,
                is_reusable=bool(limit and limit > 1),
            )
            await uow.promocodes.add(promo)
            summary["promocodes"] += 1
