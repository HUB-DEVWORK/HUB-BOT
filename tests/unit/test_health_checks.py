"""Deep-health worker-heartbeat staleness logic (tasks.worker_heartbeat ↔ check_worker)."""

from __future__ import annotations

import time

from src.infrastructure.services.health import check_worker


class _Redis:
    def __init__(self, value: str | bytes | None) -> None:
        self._value = value

    async def get(self, key: str) -> str | bytes | None:
        return self._value


async def test_fresh_heartbeat_is_alive() -> None:
    assert await check_worker(_Redis(str(int(time.time())))) is True


async def test_bytes_heartbeat_is_decoded() -> None:
    assert await check_worker(_Redis(str(int(time.time())).encode())) is True


async def test_stale_heartbeat_is_dead() -> None:
    assert await check_worker(_Redis(str(int(time.time()) - 3600))) is False


async def test_missing_heartbeat_is_dead() -> None:
    assert await check_worker(_Redis(None)) is False


async def test_garbage_heartbeat_is_dead() -> None:
    assert await check_worker(_Redis("not-a-number")) is False


async def test_no_get_method_is_dead() -> None:
    assert await check_worker(object()) is False
