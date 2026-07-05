"""ReferralService: binding + at-most-once commission (gotcha #13)."""

from __future__ import annotations

from src.application.services.referral import DEFAULT_COMMISSION_PERCENT, ReferralService
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_user
from tests.fakes import RecordingEventBus


async def test_bind_sets_referrer_once(uow: UnitOfWork) -> None:
    svc = ReferralService(RecordingEventBus())
    async with uow:
        referrer = await make_user(uow, telegram_id=1)
        invited = await make_user(uow, telegram_id=2)
        await uow.commit()
        first = await svc.bind(uow, invited, referrer.referral_code)
        assert first is not None
        # a second bind attempt is ignored (one referrer per user)
        second = await svc.bind(uow, invited, referrer.referral_code)
        assert second is None
        assert invited.referred_by_id == referrer.id


async def test_commission_paid_once_per_transaction(uow: UnitOfWork) -> None:
    svc = ReferralService(RecordingEventBus())
    async with uow:
        referrer = await make_user(uow, telegram_id=1)
        invited = await make_user(uow, telegram_id=2)
        await uow.commit()
        await svc.bind(uow, invited, referrer.referral_code)
        await uow.commit()

        earning = await svc.reward_on_topup(
            uow, payer=invited, amount_minor=10000, transaction_id=42
        )
        assert earning is not None
        assert earning.amount_minor == 10000 * DEFAULT_COMMISSION_PERCENT // 100
        await uow.commit()

        referrer_id = referrer.id
        # Retried webhook with the same source transaction must not double-pay.
        again = await svc.reward_on_topup(uow, payer=invited, amount_minor=10000, transaction_id=42)
        await uow.commit()

    async with uow:
        refreshed = await uow.users.get(referrer_id)
        assert refreshed is not None
        assert refreshed.balance_minor == 2500  # credited exactly once
        assert again is earning or again.id == earning.id
