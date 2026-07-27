"""Live traffic refresh for the subscription screens.

The local ``traffic_used_bytes`` is written only by inbound panel webhooks, and those
fire on state changes, not on traffic ticks. Between events the stored number goes
stale, and users see days-old usage in the bot. On screen render we pull the live value
from the panel, remember the moment in Redis so hot screens don't hammer the panel,
and fall back to the stored value when the panel is slow or down, so the screen
renders either way.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.application.common.cache import KeyValueCache
from src.application.common.panel import RemnawaveClient
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.infrastructure.database.models.subscription import Subscription

log = get_logger("traffic")

_CACHE_PREFIX = "trafcache:"


class TrafficService:
    """Fetch-and-store fresh panel usage for one subscription."""

    def __init__(
        self,
        client: RemnawaveClient,
        *,
        cache: KeyValueCache | None = None,
        ttl_seconds: int = 60,
        panel_timeout: float = 1.5,
    ) -> None:
        self._client = client
        self._cache = cache
        self._ttl = ttl_seconds
        self._timeout = panel_timeout

    async def refresh_used_bytes(self, sub: Subscription) -> int:
        """Refresh ``sub.traffic_used_bytes`` from the panel; returns the current value.

        Cache-hit means we refreshed this sub less than TTL ago: the DB copy can only be
        same-or-newer (webhooks write it too), so the panel round-trip is skipped and the
        stored value stands. The caller owns the commit after a live refresh.
        """
        if sub.remnawave_uuid is None:
            return sub.traffic_used_bytes
        key = f"{_CACHE_PREFIX}{sub.id}"
        if await self._is_fresh(key):
            return sub.traffic_used_bytes
        try:
            panel = await asyncio.wait_for(
                self._client.get_user_by_uuid(sub.remnawave_uuid), timeout=self._timeout
            )
        except Exception as exc:
            log.warning("traffic_refresh_failed", sub_id=sub.id, error=str(exc)[:200])
            return sub.traffic_used_bytes
        if panel is None:
            return sub.traffic_used_bytes
        sub.traffic_used_bytes = panel.traffic_used_bytes
        await self._remember(key, panel.traffic_used_bytes)
        return panel.traffic_used_bytes

    async def _is_fresh(self, key: str) -> bool:
        if self._cache is None:
            return False
        try:
            return await self._cache.get(key) is not None
        except Exception:  # cache down must not block the screen
            return False

    async def _remember(self, key: str, value: int) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set(key, str(value), ex=self._ttl)
        except Exception:  # best-effort: worst case is one extra panel call
            log.warning("traffic_cache_write_failed", key=key)
