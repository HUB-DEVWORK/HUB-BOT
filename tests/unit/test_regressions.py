"""Regression tests for bugs found in the adversarial review.

- uq_active_sub partial-unique index actually enforces one live subscription per (user, plan)
  (was dead: enum stored NAMES 'ACTIVE' but the predicate matched lowercase values).
- Wallet credits are atomic and webhook-idempotent (DEPOSIT via PaymentService.process).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from src.application.dto.pricing import PurchaseRequest
from src.application.services.payment import PaymentService
from src.application.services.pricing import PricingService
from src.application.services.promo import PromoError, PromoService
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.enums import (
    Availability,
    Currency,
    PurchaseType,
    RewardType,
    SubscriptionStatus,
    TransactionStatus,
    TransactionType,
)
from src.infrastructure.database.models.promocode import Promocode
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient, RecordingEventBus


async def test_second_active_subscription_per_user_plan_is_rejected(uow: UnitOfWork) -> None:
    async with uow:
        user = await make_user(uow)
        plan, _ = await make_plan(uow)
        await uow.commit()

        uow.session.add(
            Subscription(
                user_id=user.id, plan_id=plan.id, short_id="aaa", status=SubscriptionStatus.ACTIVE
            )
        )
        await uow.flush()
        uow.session.add(
            Subscription(
                user_id=user.id, plan_id=plan.id, short_id="bbb", status=SubscriptionStatus.ACTIVE
            )
        )
        with pytest.raises(IntegrityError):  # partial-unique index now enforces the invariant
            await uow.flush()


async def test_expired_subscription_does_not_block_a_new_active_one(uow: UnitOfWork) -> None:
    async with uow:
        user = await make_user(uow)
        plan, _ = await make_plan(uow)
        await uow.commit()
        uow.session.add(
            Subscription(
                user_id=user.id, plan_id=plan.id, short_id="old", status=SubscriptionStatus.EXPIRED
            )
        )
        uow.session.add(
            Subscription(
                user_id=user.id, plan_id=plan.id, short_id="new", status=SubscriptionStatus.ACTIVE
            )
        )
        await uow.flush()  # expired one is outside the partial index -> no conflict
        await uow.commit()


def _payment_service() -> PaymentService:
    bus = RecordingEventBus()
    subs = SubscriptionService(RemnawaveService(FakeRemnawaveClient()))
    return PaymentService(PurchaseService(PricingService(), subs, bus), bus, ReferralService(bus))


async def test_deposit_credits_balance_and_is_idempotent(uow: UnitOfWork) -> None:
    payments = _payment_service()
    async with uow:
        user = await make_user(uow, balance_minor=0)
        txn = Transaction(
            user_id=user.id,
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            amount_minor=5000,
            currency=Currency.RUB,
        )
        await uow.transactions.add(txn)
        await uow.commit()
        user_id, payment_id = user.id, txn.payment_id

    async with uow:
        assert await payments.process(
            uow, payment_id=payment_id, status=TransactionStatus.COMPLETED
        )
        await uow.commit()
    async with uow:
        u = await uow.users.get(user_id)
        assert u is not None and u.balance_minor == 5000 and u.has_made_first_topup

    # duplicate webhook must not double-credit
    async with uow:
        again = await payments.process(
            uow, payment_id=payment_id, status=TransactionStatus.COMPLETED
        )
        await uow.commit()
    assert again is False
    async with uow:
        u = await uow.users.get(user_id)
        assert u is not None and u.balance_minor == 5000


async def test_invited_promocode_requires_a_referrer(uow: UnitOfWork) -> None:
    svc = PromoService()
    async with uow:
        referrer = await make_user(uow, telegram_id=1)
        await uow.commit()
        plain = await make_user(uow, telegram_id=2)
        invited = await make_user(uow, telegram_id=3, referred_by_id=referrer.id)
        await uow.promocodes.add(
            Promocode(
                code="INV",
                reward_type=RewardType.BALANCE,
                reward_value=1000,
                availability=Availability.INVITED,
            )
        )
        await uow.commit()

        with pytest.raises(PromoError):  # non-referred user is rejected
            await svc.apply(uow, plain, "INV")
        assert await svc.apply(uow, invited, "INV") is RewardType.BALANCE
        assert invited.balance_minor == 1000


def _purchase_and_payment() -> tuple[PurchaseService, PaymentService, FakeRemnawaveClient]:
    fake = FakeRemnawaveClient()
    bus = RecordingEventBus()
    subs = SubscriptionService(RemnawaveService(fake))
    purchase = PurchaseService(PricingService(), subs, bus)
    return purchase, PaymentService(purchase, bus, ReferralService(bus)), fake


async def test_paid_renew_extends_and_does_not_duplicate(uow: UnitOfWork) -> None:
    purchase, payments, fake = _purchase_and_payment()

    async def pay(req: PurchaseRequest) -> None:
        async with uow:
            txn, _ = await purchase.start(uow, req)
            await uow.commit()
            payment_id = txn.payment_id
        async with uow:
            await payments.process(uow, payment_id=payment_id, status=TransactionStatus.COMPLETED)
            await uow.commit()

    async with uow:
        user = await make_user(uow)
        plan, _ = await make_plan(uow, price_minor=30000, days=30)
        await uow.commit()
        user_id, plan_id = user.id, plan.id

    await pay(
        PurchaseRequest(user_id=user_id, plan_id=plan_id, duration_days=30, currency=Currency.RUB)
    )
    async with uow:
        subs = await uow.subscriptions.active_for_user(user_id)
        assert len(subs) == 1
        sub_id, first_expire = subs[0].id, subs[0].expire_at
    assert len(fake.users) == 1

    await pay(
        PurchaseRequest(
            user_id=user_id,
            plan_id=plan_id,
            duration_days=30,
            currency=Currency.RUB,
            purchase_type=PurchaseType.RENEW,
            subscription_id=sub_id,
        )
    )
    async with uow:
        subs2 = await uow.subscriptions.active_for_user(user_id)
    assert len(subs2) == 1  # extended, NOT a second subscription
    assert subs2[0].id == sub_id
    assert first_expire is not None and subs2[0].expire_at is not None
    assert subs2[0].expire_at > first_expire
    assert len(fake.users) == 1  # no second panel user provisioned
