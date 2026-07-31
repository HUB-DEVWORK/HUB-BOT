"""Grace-окно («спасательный круг»): кто в него попадает, как выходит и почему его нельзя фармить.

Обкатано на боевом магазине (Lazeyka) до попадания в продукт; тесты фиксируют гарантии, на
которые опирается деньгозначимая логика: триалам grace не даётся, бэклог просроченных при
включении фичи не воскресает, повторный вход ограничен кулдауном.
"""

from __future__ import annotations

import datetime as dt
import uuid as uuid_mod

from src.application.services.ids import generate_short_id
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.enums import SubscriptionStatus
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_user
from tests.fakes import FakeRemnawaveClient

LOOKBACK = dt.timedelta(days=2)


async def _sub(
    uow: UnitOfWork,
    *,
    telegram_id: int,
    expired_ago: dt.timedelta,
    is_trial: bool = False,
    grace_until: dt.datetime | None = None,
    grace_started_at: dt.datetime | None = None,
    status: SubscriptionStatus = SubscriptionStatus.EXPIRED,
    with_panel: bool = True,
) -> Subscription:
    user = await make_user(uow, telegram_id=telegram_id)
    sub = Subscription(
        user_id=user.id,
        remnawave_uuid=uuid_mod.uuid4() if with_panel else None,
        short_id=generate_short_id(),
        status=status,
        is_trial=is_trial,
        expire_at=dt.datetime.now(dt.UTC) - expired_ago,
        grace_until=grace_until,
        grace_started_at=grace_started_at,
    )
    await uow.subscriptions.add(sub)
    await uow.flush()
    return sub


async def test_only_eligible_paid_subs_enter_grace(uow: UnitOfWork) -> None:
    """Триалы, непровизиженные и давно просроченные в выборку не попадают."""
    now = dt.datetime.now(dt.UTC)
    async with uow:
        fresh = await _sub(uow, telegram_id=9001, expired_ago=dt.timedelta(hours=3))
        await _sub(uow, telegram_id=9002, expired_ago=dt.timedelta(hours=3), is_trial=True)
        await _sub(uow, telegram_id=9003, expired_ago=dt.timedelta(days=30))  # старый бэклог
        await _sub(uow, telegram_id=9004, expired_ago=dt.timedelta(hours=3), with_panel=False)
        await uow.commit()

        rows = await uow.subscriptions.grace_candidates(
            now, lookback=LOOKBACK, cooldown_cutoff=now - dt.timedelta(days=30), limit=100
        )
    assert [s.id for s in rows] == [fresh.id]


async def test_cooldown_blocks_farming_grace(uow: UnitOfWork) -> None:
    """Недавно отгулявший grace второй раз подряд его не получает, давний — получает."""
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=30)
    async with uow:
        recent = await _sub(
            uow,
            telegram_id=9101,
            expired_ago=dt.timedelta(hours=2),
            grace_started_at=now - dt.timedelta(days=3),  # свежий кулдаун
        )
        old = await _sub(
            uow,
            telegram_id=9102,
            expired_ago=dt.timedelta(hours=2),
            grace_started_at=now - dt.timedelta(days=90),
        )
        await uow.commit()
        ids = {
            s.id
            for s in await uow.subscriptions.grace_candidates(
                now, lookback=LOOKBACK, cooldown_cutoff=cutoff, limit=100
            )
        }
    assert old.id in ids
    assert recent.id not in ids


async def test_sub_already_in_grace_is_not_re_entered(uow: UnitOfWork) -> None:
    now = dt.datetime.now(dt.UTC)
    async with uow:
        await _sub(
            uow,
            telegram_id=9201,
            expired_ago=dt.timedelta(hours=1),
            grace_until=now + dt.timedelta(days=3),
        )
        await uow.commit()
        rows = await uow.subscriptions.grace_candidates(
            now, lookback=LOOKBACK, cooldown_cutoff=now - dt.timedelta(days=30), limit=100
        )
    assert rows == []


async def test_enter_then_end_grace_roundtrip(uow: UnitOfWork) -> None:
    """Вход в grace открывает доступ с урезанными лимитами, выход — закрывает и помечает EXPIRED,
    сохраняя grace_started_at для кулдауна."""
    fake = FakeRemnawaveClient()
    svc = SubscriptionService(RemnawaveService(fake))
    async with uow:
        sub = await _sub(uow, telegram_id=9301, expired_ago=dt.timedelta(hours=1))
        panel_uuid = sub.remnawave_uuid
        await uow.commit()

        await svc.enter_grace(uow, sub, days=5, traffic_gb=1, telegram_id=9301)
        await uow.commit()
        assert sub.grace_until is not None and sub.grace_until > dt.datetime.now(dt.UTC)
        assert sub.grace_started_at is not None
        assert sub.remnawave_uuid == panel_uuid  # та же учётка на панели, без дублей

        started_at = sub.grace_started_at
        await svc.end_grace(uow, sub)
        await uow.commit()
    assert sub.grace_until is None
    assert sub.status is SubscriptionStatus.EXPIRED
    assert sub.grace_started_at == started_at  # кулдаун переживает выход
