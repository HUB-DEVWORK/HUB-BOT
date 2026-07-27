"""TrafficService: live panel usage on screen render, cache throttle, graceful fallback."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid as uuid_mod
from typing import Any

from src.application.dto.panel import PanelUser
from src.application.dto.pricing import PurchaseRequest
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.application.services.traffic import TrafficService
from src.core.enums import Currency
from src.core.exceptions import RemnawaveError
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient

GIB = 1024**3


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value


class _CountingClient(FakeRemnawaveClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def get_user_by_uuid(self, panel_uuid: uuid_mod.UUID) -> PanelUser | None:
        self.calls += 1
        return await super().get_user_by_uuid(panel_uuid)


async def _grant(uow: UnitOfWork, fake: FakeRemnawaveClient) -> Any:
    subs = SubscriptionService(RemnawaveService(fake))
    user = await make_user(uow)
    plan, _ = await make_plan(uow)
    await uow.commit()
    req = PurchaseRequest(user_id=user.id, plan_id=plan.id, duration_days=30, currency=Currency.RUB)
    sub = await subs.grant(uow, user=user, plan=plan, req=req)
    await uow.commit()
    return sub


def _wind_traffic(fake: FakeRemnawaveClient, panel_uuid: uuid_mod.UUID, used: int) -> None:
    fake.users[panel_uuid] = dataclasses.replace(fake.users[panel_uuid], traffic_used_bytes=used)


async def test_refresh_pulls_live_value_and_stores_it(uow: UnitOfWork) -> None:
    async with uow:
        fake = FakeRemnawaveClient()
        sub = await _grant(uow, fake)
        assert sub.traffic_used_bytes == 0
        _wind_traffic(fake, sub.remnawave_uuid, 7 * GIB)

        service = TrafficService(fake)
        got = await service.refresh_used_bytes(sub)

        assert got == 7 * GIB
        assert sub.traffic_used_bytes == 7 * GIB


async def test_refresh_survives_panel_error(uow: UnitOfWork) -> None:
    class _DownClient(FakeRemnawaveClient):
        async def get_user_by_uuid(self, panel_uuid: uuid_mod.UUID) -> PanelUser | None:
            raise RemnawaveError("panel down")

    async with uow:
        fake = _DownClient()
        sub = await _grant(uow, fake)
        sub.traffic_used_bytes = 3 * GIB
        await uow.commit()

        got = await TrafficService(fake).refresh_used_bytes(sub)

        assert got == 3 * GIB  # fallback, no exception out of the render path
        assert sub.traffic_used_bytes == 3 * GIB


async def test_refresh_survives_vanished_panel_user(uow: UnitOfWork) -> None:
    async with uow:
        fake = FakeRemnawaveClient()
        sub = await _grant(uow, fake)
        sub.traffic_used_bytes = 2 * GIB
        del fake.users[sub.remnawave_uuid]

        got = await TrafficService(fake).refresh_used_bytes(sub)

        assert got == 2 * GIB


async def test_refresh_times_out_slow_panel(uow: UnitOfWork) -> None:
    class _SlowClient(FakeRemnawaveClient):
        async def get_user_by_uuid(self, panel_uuid: uuid_mod.UUID) -> PanelUser | None:
            await asyncio.sleep(0.2)
            return await super().get_user_by_uuid(panel_uuid)

    async with uow:
        fake = _SlowClient()
        sub = await _grant(uow, fake)
        sub.traffic_used_bytes = 1 * GIB
        _wind_traffic(fake, sub.remnawave_uuid, 9 * GIB)

        got = await TrafficService(fake, panel_timeout=0.01).refresh_used_bytes(sub)

        assert got == 1 * GIB  # timed out -> stored value, screen still renders


async def test_cache_hit_skips_panel_roundtrip(uow: UnitOfWork) -> None:
    async with uow:
        fake = _CountingClient()
        sub = await _grant(uow, fake)
        _wind_traffic(fake, sub.remnawave_uuid, 5 * GIB)
        cache = _FakeCache()
        service = TrafficService(fake, cache=cache)

        first = await service.refresh_used_bytes(sub)
        calls_after_first = fake.calls
        second = await service.refresh_used_bytes(sub)

        assert first == 5 * GIB
        assert second == 5 * GIB  # DB value stands while the cache marker is fresh
        assert fake.calls == calls_after_first  # no second panel call


async def test_no_panel_uuid_returns_local(uow: UnitOfWork) -> None:
    async with uow:
        fake = _CountingClient()
        sub = await _grant(uow, fake)
        sub.remnawave_uuid = None
        sub.traffic_used_bytes = 4 * GIB
        before = fake.calls

        got = await TrafficService(fake).refresh_used_bytes(sub)

        assert got == 4 * GIB
        assert fake.calls == before


async def test_cache_write_failure_is_best_effort(uow: UnitOfWork) -> None:
    class _BrokenCache(_FakeCache):
        async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
            raise ConnectionError("redis down")

    async with uow:
        fake = FakeRemnawaveClient()
        sub = await _grant(uow, fake)
        _wind_traffic(fake, sub.remnawave_uuid, 6 * GIB)

        got = await TrafficService(fake, cache=_BrokenCache()).refresh_used_bytes(sub)

        assert got == 6 * GIB
        assert sub.traffic_used_bytes == 6 * GIB
