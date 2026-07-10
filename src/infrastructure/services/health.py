"""Health checks for the DB and Redis (surfaced by the web /health endpoint)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True, slots=True)
class HealthReport:
    database: bool
    redis: bool

    @property
    def ok(self) -> bool:
        return self.database and self.redis


async def check_database(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def check_redis(redis: object) -> bool:
    ping = getattr(redis, "ping", None)
    if ping is None:
        return False
    try:
        await ping()
        return True
    except Exception:
        return False


# The worker stamps ``worker:heartbeat`` every minute (tasks.worker_heartbeat). A missing or
# stale stamp means the worker — which /health can't observe (web stays up) — is down, silently
# stopping provisioning-reconcile, backups, autopay and reminders.
_HEARTBEAT_STALE_SECONDS = 180


async def check_worker(redis: object) -> bool:
    import time

    get = getattr(redis, "get", None)
    if get is None:
        return False
    try:
        raw = await get("worker:heartbeat")
    except Exception:
        return False
    if raw is None:
        return False
    try:
        stamped = int(raw.decode() if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - stamped) < _HEARTBEAT_STALE_SECONDS


async def check_panel(container: object) -> bool:
    """Best-effort panel reachability (informational — a panel outage is handled by
    maintenance mode, not by failing web health)."""
    client = getattr(container, "remnawave_client", None)
    if client is None or not hasattr(client, "get_version"):
        return False
    try:
        await client.get_version()
        return True
    except Exception:
        return False
