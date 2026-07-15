"""Redis client factory."""

from __future__ import annotations

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    # Retry on transient connection blips (e.g. a docker-network "Temporary failure in name
    # resolution" while the redis container restarts) instead of surfacing them as a crash.
    return Redis.from_url(
        settings.redis.url,
        decode_responses=True,
        retry=Retry(ExponentialBackoff(cap=1.0, base=0.05), retries=3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
        health_check_interval=30,
    )
