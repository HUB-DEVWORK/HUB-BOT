"""Concrete Remnawave HTTP client (httpx) implementing the RemnawaveClient protocol.

Retries transient failures with jittered backoff; raises hard on auth errors. Endpoint paths
and JSON field names are centralized in ``_PATHS`` / the mappers below — align them to your
panel version if the smoke test reports a mismatch (docs/context/01).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
import uuid
from typing import Any

import httpx

from src.application.dto.panel import (
    PanelDevice,
    PanelNode,
    PanelSquad,
    PanelUser,
    PanelVersion,
    ProvisionSpec,
)
from src.core.constants import MIN_REMNAWAVE_VERSION, PANEL_RETRY_ATTEMPTS, PANEL_RETRY_BASE_DELAY
from src.core.exceptions import RemnawaveAuthError, RemnawaveError, RemnawaveTransientError
from src.core.logging import get_logger
from src.infrastructure.remnawave.connection import ConnectionProfile

log = get_logger(__name__)

_PATHS = {
    "health": "/api/system/health",
    "stats": "/api/system/stats",
    "users": "/api/users",
    "user": "/api/users/{uuid}",
    "user_by_tg": "/api/users/by-telegram-id/{telegram_id}",
    "user_actions": "/api/users/{uuid}/actions/{action}",
    "internal_squads": "/api/internal-squads",
    "nodes": "/api/nodes",
}


def _unwrap(data: Any) -> Any:
    """Remnawave wraps payloads in ``{"response": ...}``; tolerate both shapes."""
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


def _parse_dt(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _used_bytes(data: dict[str, Any]) -> int:
    """Read used traffic. On Remnawave the user carries ``userTraffic`` (number or object);
    fall back to the older ``trafficUsedBytes`` / ``usedTrafficBytes`` spellings."""
    value: Any = data.get("userTraffic")
    if isinstance(value, dict):
        value = value.get("total") or value.get("used") or value.get("usedBytes") or 0
    if value is None:
        value = data.get("trafficUsedBytes") or data.get("usedTrafficBytes") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_panel_user(data: dict[str, Any]) -> PanelUser:
    # Field names verified against a live panel (see docs/context/01): shortUuid,
    # externalSquadUuid, userTraffic — with legacy fallbacks kept for older versions.
    return PanelUser(
        uuid=uuid.UUID(str(data["uuid"])),
        short_id=str(data.get("shortUuid") or data.get("shortId") or ""),
        username=str(data.get("username") or ""),
        is_enabled=str(data.get("status", "ACTIVE")).upper() == "ACTIVE",
        expire_at=_parse_dt(data.get("expireAt") or data.get("expire_at")),
        traffic_limit_bytes=int(data.get("trafficLimitBytes") or 0),
        traffic_used_bytes=_used_bytes(data),
        device_limit=data.get("hwidDeviceLimit"),
        subscription_url=data.get("subscriptionUrl") or data.get("subscription_url"),
        telegram_id=data.get("telegramId"),
        internal_squads=tuple(str(s) for s in data.get("activeInternalSquads") or []),
        external_squad=data.get("externalSquadUuid") or data.get("activeExternalSquad"),
        tag=data.get("tag"),
    )


def _spec_payload(spec: ProvisionSpec) -> dict[str, Any]:
    # Create/update INPUT field names. Read-side names are panel-verified; the write side
    # still needs confirmation on a test panel (do not test writes on production).
    payload: dict[str, Any] = {
        "username": spec.username,
        "expireAt": spec.expire_at.astimezone(dt.UTC).isoformat(),
        "trafficLimitBytes": spec.traffic_limit_bytes,
    }
    # Send squad membership ONLY when we actually have one. An empty list REPLACES the
    # panel set with none (kicking the user off every server) — fatal on update/resync for
    # migrated subs whose local squad list is empty while the adopted panel user has real
    # squads. Empty ⇒ omit ⇒ "leave alone" (same rule as device_limit/external_squad below).
    if spec.internal_squads:
        payload["activeInternalSquads"] = list(spec.internal_squads)
    if spec.telegram_id is not None:
        payload["telegramId"] = spec.telegram_id
    # device_limit / external_squad follow the same omit ⇒ "leave alone" rule as squads,
    # EXCEPT on a plan CHANGE: the reset_* flags let the new plan actively clear a stale
    # cap/exit that the old plan set (unlimited devices ⇒ 0, no exit ⇒ null). Without them a
    # None/falsy value is omitted and the panel keeps the old value (create/renew rely on that).
    if spec.device_limit is not None:
        payload["hwidDeviceLimit"] = spec.device_limit
    elif spec.reset_device_limit:
        payload["hwidDeviceLimit"] = 0  # Remnawave: 0 == unlimited devices
    if spec.external_squad:
        payload["externalSquadUuid"] = spec.external_squad
    elif spec.reset_external_squad:
        payload["externalSquadUuid"] = None
    if spec.description:
        payload["description"] = spec.description
    # Caller-supplied passthrough fields (e.g. vlessUuid/shortUuid/status on import).
    payload.update(spec.extra)
    return payload


class RemnawaveHttpClient:
    """Async httpx client. Construct via :meth:`from_profile` (or DI)."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @classmethod
    def from_profile(
        cls, profile: ConnectionProfile, *, timeout: float = 15.0
    ) -> RemnawaveHttpClient:
        client = httpx.AsyncClient(
            base_url=profile.base_url,
            headers=profile.headers,
            cookies=profile.cookies,
            verify=profile.verify,
            timeout=timeout,
        )
        return cls(client)

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- low-level request with retry -------------------------------------
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, PANEL_RETRY_ATTEMPTS + 1):
            try:
                resp = await self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:  # timeouts, connection errors
                last_exc = RemnawaveTransientError(str(exc))
            else:
                if resp.status_code in (401, 403):
                    raise RemnawaveAuthError(f"panel rejected credentials ({resp.status_code})")
                if resp.status_code >= 500:
                    last_exc = RemnawaveTransientError(f"panel {resp.status_code}")
                elif resp.status_code >= 400:
                    raise RemnawaveError(f"panel {resp.status_code}: {resp.text[:200]}")
                else:
                    return _unwrap(resp.json()) if resp.content else None
            # backoff before retrying a transient failure
            if attempt < PANEL_RETRY_ATTEMPTS:
                delay = PANEL_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                await asyncio.sleep(delay + random.uniform(0, delay / 2))
        raise last_exc or RemnawaveTransientError("panel request failed")

    # --- protocol methods -------------------------------------------------
    async def get_version(self) -> PanelVersion:
        data = await self._request("GET", _PATHS["health"])
        raw = ""
        if isinstance(data, dict):
            raw = str(data.get("version") or data.get("appVersion") or "")
        parts = [int(p) for p in raw.split(".")[:3] if p.isdigit()]
        known = bool(parts)  # did we actually read a version string?
        while len(parts) < 3:
            parts.append(0)
        major, minor, patch = parts[0], parts[1], parts[2]
        caps: set[str] = set()
        # Capability-probe, not a pin: only assume the legacy behaviour when the panel
        # *told us* it's old. Newer backends (v2) don't expose a version here — an
        # unreadable version must NOT be treated as pre-2.8 (that added happ_encrypt,
        # which 2.x rejects). Unknown → assume modern.
        if known and (major, minor, patch) < MIN_REMNAWAVE_VERSION:
            caps.add("happ_encrypt")  # removed in 2.8.0
        return PanelVersion(
            raw=raw or "0.0.0", major=major, minor=minor, patch=patch, capabilities=frozenset(caps)
        )

    async def create_user(self, spec: ProvisionSpec) -> PanelUser:
        data = await self._request("POST", _PATHS["users"], json=_spec_payload(spec))
        return _to_panel_user(dict(data))

    async def update_user(self, panel_uuid: uuid.UUID, spec: ProvisionSpec) -> PanelUser:
        # Backend v2 updates a user via PATCH /api/users with the uuid IN THE BODY —
        # PATCH /api/users/{uuid} 404s. (Verified against a live 2.x panel.)
        payload = _spec_payload(spec) | {"uuid": str(panel_uuid)}
        data = await self._request("PATCH", _PATHS["users"], json=payload)
        return _to_panel_user(dict(data))

    async def get_user_by_uuid(self, panel_uuid: uuid.UUID) -> PanelUser | None:
        try:
            data = await self._request("GET", _PATHS["user"].format(uuid=panel_uuid))
        except RemnawaveError:
            return None
        return _to_panel_user(dict(data)) if data else None

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser | None:
        try:
            data = await self._request("GET", _PATHS["user_by_tg"].format(telegram_id=telegram_id))
        except RemnawaveError:
            return None
        if not data:
            return None
        if isinstance(data, list):
            return _to_panel_user(dict(data[0])) if data else None
        return _to_panel_user(dict(data))

    async def _action(self, panel_uuid: uuid.UUID, action: str) -> None:
        await self._request("POST", _PATHS["user_actions"].format(uuid=panel_uuid, action=action))

    async def enable_user(self, panel_uuid: uuid.UUID) -> None:
        await self._action(panel_uuid, "enable")

    async def disable_user(self, panel_uuid: uuid.UUID) -> None:
        await self._action(panel_uuid, "disable")

    async def delete_user(self, panel_uuid: uuid.UUID) -> None:
        await self._request("DELETE", _PATHS["user"].format(uuid=panel_uuid))

    async def reset_traffic(self, panel_uuid: uuid.UUID) -> None:
        await self._action(panel_uuid, "reset-traffic")

    async def revoke_subscription(self, panel_uuid: uuid.UUID) -> PanelUser:
        data = await self._request(
            "POST", _PATHS["user_actions"].format(uuid=panel_uuid, action="revoke")
        )
        return _to_panel_user(dict(data))

    async def drop_connections(self, panel_uuid: uuid.UUID) -> None:
        await self._action(panel_uuid, "drop-connections")

    async def get_devices(self, panel_uuid: uuid.UUID) -> list[PanelDevice]:
        """HWID devices of one panel user (GET /api/hwid/devices/{uuid})."""
        data = await self._request("GET", f"/api/hwid/devices/{panel_uuid}")
        raw = data.get("devices") if isinstance(data, dict) else data
        devices: list[PanelDevice] = []
        for item in raw or []:
            if not isinstance(item, dict) or not item.get("hwid"):
                continue
            devices.append(
                PanelDevice(
                    hwid=str(item["hwid"]),
                    platform=item.get("platform"),
                    device_model=item.get("deviceModel") or item.get("model"),
                    created_at=item.get("createdAt"),
                )
            )
        return devices

    async def delete_device(self, panel_uuid: uuid.UUID, hwid: str) -> None:
        """Unbind one HWID (POST /api/hwid/devices/delete — panel-verified route)."""
        await self._request(
            "POST",
            "/api/hwid/devices/delete",
            json={"userUuid": str(panel_uuid), "hwid": hwid},
        )

    async def start_users_ips_job(self, node_uuid: str) -> str:
        """Kick the panel's online-IP collection for a node (ip-control API)."""
        data = await self._request("POST", f"/api/ip-control/fetch-users-ips/{node_uuid}")
        return str((data or {}).get("jobId") or "")

    async def get_users_ips_result(self, job_id: str) -> list[tuple[str, list[str]]] | None:
        """None while the job is running; [(userId, [ips])] when completed."""
        data = await self._request("GET", f"/api/ip-control/fetch-users-ips/result/{job_id}")
        data = data or {}
        if not data.get("isCompleted"):
            return None
        if data.get("isFailed"):
            return []
        users = (data.get("result") or {}).get("users") or []
        out: list[tuple[str, list[str]]] = []
        for u in users:
            ips = [str(i.get("ip")) for i in (u.get("ips") or []) if i.get("ip")]
            if u.get("userId"):
                out.append((str(u["userId"]), ips))
        return out

    async def get_internal_squads(self) -> list[PanelSquad]:
        data = await self._request("GET", _PATHS["internal_squads"])
        items = data.get("internalSquads", data) if isinstance(data, dict) else data
        return [
            PanelSquad(
                uuid=uuid.UUID(str(s["uuid"])),
                name=str(s.get("name") or ""),
                members_count=int(s.get("membersCount") or 0),
            )
            for s in (items or [])
        ]

    async def get_nodes(self) -> list[PanelNode]:
        data = await self._request("GET", _PATHS["nodes"])
        items = data if isinstance(data, list) else data.get("nodes", []) if data else []
        return [
            PanelNode(
                uuid=uuid.UUID(str(n["uuid"])),
                name=str(n.get("name") or ""),
                is_online=bool(n.get("isConnected") or n.get("isOnline")),
                country_code=(n.get("countryCode") or None),
                address=(n.get("address") or None),
                users_online=int(n.get("usersOnline") or 0),
                traffic_used_bytes=int(n.get("trafficUsedBytes") or 0),
                is_disabled=bool(n.get("isDisabled")),
            )
            for n in items
        ]
