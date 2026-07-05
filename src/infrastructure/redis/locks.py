"""Distributed lock helper (SET NX PX) for webhook / torrent-blocker dedup (gotcha #18)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis


@asynccontextmanager
async def redis_lock(redis: Redis, key: str, *, ttl_seconds: int = 30) -> AsyncIterator[bool]:
    """Acquire a best-effort lock. Yields True if acquired, False if already held.

    Used to dedup repeated webhook deliveries; the caller no-ops when it yields False.
    """
    acquired = bool(await redis.set(key, "1", nx=True, ex=ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            await redis.delete(key)
