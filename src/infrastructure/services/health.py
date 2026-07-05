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
