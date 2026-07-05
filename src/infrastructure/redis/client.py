"""Redis client factory."""

from __future__ import annotations

from redis.asyncio import Redis

from src.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis.url, decode_responses=True)
