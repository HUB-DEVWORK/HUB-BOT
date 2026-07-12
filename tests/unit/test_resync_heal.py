"""RemnawaveResyncService heal edge cases: never re-enable a traffic-capped (LIMITED) user."""

from __future__ import annotations

import dataclasses

from src.application.dto.pricing import PurchaseRequest
from src.application.services.remnawave import RemnawaveService
from src.application.services.resync import RemnawaveResyncService
from src.application.services.subscription import SubscriptionService
from src.core.enums import Currency, SubscriptionStatus
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient


async def _grant(uow: UnitOfWork):  # type: ignore[no-untyped-def]
    fake = FakeRemnawaveClient()
    subs = SubscriptionService(RemnawaveService(fake))
    user = await make_user(uow)
    plan, _ = await make_plan(uow)
    await uow.commit()
    req = PurchaseRequest(user_id=user.id, plan_id=plan.id, duration_days=30, currency=Currency.RUB)
    sub = await subs.grant(uow, user=user, plan=plan, req=req)
    await uow.commit()
    return fake, subs, sub


async def test_resync_skips_traffic_limited_user(uow: UnitOfWork) -> None:
    """A user the panel LIMITED for hitting the traffic cap must not be re-enabled: that would
    only flap them ACTIVE until the panel re-limits them next check (B7)."""
    async with uow:
        fake, subs, sub = await _grant(uow)
        service = RemnawaveResyncService(fake, subs)
        assert sub.remnawave_uuid is not None

        panel = fake.users[sub.remnawave_uuid]
        # LIMITED on the panel = not ACTIVE (is_enabled False) AND over the traffic cap. Align
        # expiry so nothing but the traffic state could trigger a heal.
        fake.users[sub.remnawave_uuid] = dataclasses.replace(
            panel,
            is_enabled=False,
            expire_at=sub.expire_at,
            traffic_limit_bytes=1_000,
            traffic_used_bytes=1_000,
        )

        report = await service.resync(uow)
        assert report.checked == 1
        assert report.healed == 0  # left alone — the panel is enforcing the cap correctly
        # untouched: push_limits / enable_user never ran, so it stays LIMITED
        assert fake.users[sub.remnawave_uuid].is_enabled is False


async def test_resync_still_heals_disabled_within_cap(uow: UnitOfWork) -> None:
    """A user disabled by hand (still under the traffic cap) is a genuine drift and must heal —
    the LIMITED guard must not swallow the legitimate re-enable path."""
    async with uow:
        fake, subs, sub = await _grant(uow)
        service = RemnawaveResyncService(fake, subs)
        assert sub.remnawave_uuid is not None

        panel = fake.users[sub.remnawave_uuid]
        fake.users[sub.remnawave_uuid] = dataclasses.replace(
            panel,
            is_enabled=False,
            expire_at=sub.expire_at,
            traffic_limit_bytes=1_000,
            traffic_used_bytes=10,  # nowhere near the cap → not LIMITED, just disabled
        )

        report = await service.resync(uow)
        assert report.healed == 1
        # push_limits re-applied our authoritative spec → panel user is enabled again
        assert fake.users[sub.remnawave_uuid].is_enabled is True


async def test_reconcile_disabled_redisables_panel_user(uow: UnitOfWork) -> None:
    """Refund/revoke backstop: a locally-DISABLED sub whose panel user is still enabled (the
    panel `disable` lost the race with an outage) must be re-disabled — else the refunded
    customer keeps connecting. resync's usable-only sweep never catches this one."""
    async with uow:
        fake, subs, sub = await _grant(uow)
        service = RemnawaveResyncService(fake, subs)
        assert sub.remnawave_uuid is not None
        # Local refund: DISABLED locally, but the panel user is still enabled (disable failed).
        sub.status = SubscriptionStatus.DISABLED
        await uow.commit()
        assert fake.users[sub.remnawave_uuid].is_enabled is True

        fixed = await service.reconcile_disabled(uow)
        assert fixed == 1
        assert fake.users[sub.remnawave_uuid].is_enabled is False  # re-disabled on the panel


async def test_reconcile_disabled_ignores_already_disabled(uow: UnitOfWork) -> None:
    """No redundant panel writes when the panel user is already disabled."""
    async with uow:
        fake, subs, sub = await _grant(uow)
        service = RemnawaveResyncService(fake, subs)
        assert sub.remnawave_uuid is not None
        sub.status = SubscriptionStatus.DISABLED
        await fake.disable_user(sub.remnawave_uuid)
        await uow.commit()

        fixed = await service.reconcile_disabled(uow)
        assert fixed == 0  # panel already disabled → nothing to do
