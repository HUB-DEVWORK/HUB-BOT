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


def _to_panel_user(data: dict[str, Any]) -> PanelUser:
    return PanelUser(
        uuid=uuid.UUID(str(data["uuid"])),
        short_id=str(data.get("shortId") or data.get("short_id") or ""),
        username=str(data.get("username") or ""),
        is_enabled=str(data.get("status", "ACTIVE")).upper() == "ACTIVE",
        expire_at=_parse_dt(data.get("expireAt") or data.get("expire_at")),
        traffic_limit_bytes=int(data.get("trafficLimitBytes") or 0),
        traffic_used_bytes=int(data.get("usedTrafficBytes") or 0),
        device_limit=data.get("hwidDeviceLimit"),
        subscription_url=data.get("subscriptionUrl") or data.get("subscription_url"),
        telegram_id=data.get("telegramId"),
        internal_squads=tuple(str(s) for s in data.get("activeInternalSquads") or []),
        external_squad=data.get("activeExternalSquad"),
        tag=data.get("tag"),
    )


def _spec_payload(spec: ProvisionSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "username": spec.username,
        "expireAt": spec.expire_at.astimezone(dt.UTC).isoformat(),
        "trafficLimitBytes": spec.traffic_limit_bytes,
        "activeInternalSquads": list(spec.internal_squads),
    }
    if spec.telegram_id is not None:
        payload["telegramId"] = spec.telegram_id
    if spec.device_limit is not None:
        payload["hwidDeviceLimit"] = spec.device_limit
    if spec.external_squad:
        payload["activeExternalSquad"] = spec.external_squad
    if spec.description:
        payload["description"] = spec.description
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
        while len(parts) < 3:
            parts.append(0)
        major, minor, patch = parts[0], parts[1], parts[2]
        caps: set[str] = set()
        if (major, minor, patch) < MIN_REMNAWAVE_VERSION:
            caps.add("happ_encrypt")  # removed in 2.8.0
        return PanelVersion(
            raw=raw or "0.0.0", major=major, minor=minor, patch=patch, capabilities=frozenset(caps)
        )

    async def create_user(self, spec: ProvisionSpec) -> PanelUser:
        data = await self._request("POST", _PATHS["users"], json=_spec_payload(spec))
        return _to_panel_user(dict(data))

    async def update_user(self, panel_uuid: uuid.UUID, spec: ProvisionSpec) -> PanelUser:
        path = _PATHS["user"].format(uuid=panel_uuid)
        payload = _spec_payload(spec) | {"uuid": str(panel_uuid)}
        data = await self._request("PATCH", path, json=payload)
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
            )
            for n in items
        ]
