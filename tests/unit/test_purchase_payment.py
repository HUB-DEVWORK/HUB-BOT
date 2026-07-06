"""The purchase + payment vertical: free path, paid path, and webhook idempotency.

This is the base's most important behaviour test (gotcha #6, #14, ADR-0003/0005).
"""

from __future__ import annotations

import pytest

from src.application.dto.pricing import PurchaseRequest
from src.application.events import SubscriptionPurchased
from src.application.services.payment import PaymentService
from src.application.services.pricing import PricingService
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.enums import Currency, TransactionStatus
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient, RecordingEventBus


def _build() -> tuple[PurchaseService, PaymentService, FakeRemnawaveClient, RecordingEventBus]:
    fake = FakeRemnawaveClient()
    bus = RecordingEventBus()
    subs = SubscriptionService(RemnawaveService(fake))
    purchase = PurchaseService(PricingService(), subs, bus)
    payments = PaymentService(purchase, bus, ReferralService(bus))
    return purchase, payments, fake, bus


@pytest.fixture
def services() -> tuple[PurchaseService, PaymentService, FakeRemnawaveClient, RecordingEventBus]:
    return _build()


async def test_free_purchase_completes_inline(uow: UnitOfWork, services) -> None:
    purchase, _payments, fake, bus = services
    async with uow:
        user = await make_user(uow, personal_discount_pct=100)
        plan, _ = await make_plan(uow, price_minor=30000)
        await uow.commit()
        user_id = user.id
        req = PurchaseRequest(
            user_id=user_id, plan_id=plan.id, duration_days=30, currency=Currency.RUB
        )
        txn, quote = await purchase.start(uow, req)
        await uow.commit()

    assert quote.is_free
    assert txn.status is TransactionStatus.COMPLETED
    assert len(fake.users) == 1
    assert len(bus.of_type(SubscriptionPurchased)) == 1
    async with uow:
        assert len(await uow.subscriptions.active_for_user(user_id)) == 1


async def test_paid_purchase_pends_then_completes_and_is_idempotent(
    uow: UnitOfWork, services
) -> None:
    purchase, payments, fake, bus = services
    async with uow:
        user = await make_user(uow)
        plan, _ = await make_plan(uow, price_minor=30000)
        await uow.commit()
        user_id = user.id
        req = PurchaseRequest(
            user_id=user_id, plan_id=plan.id, duration_days=30, currency=Currency.RUB
        )
        txn, quote = await purchase.start(uow, req)
        await uow.commit()
        payment_id = txn.payment_id

    assert not quote.is_free
    assert txn.status is TransactionStatus.PENDING
    async with uow:  # nothing provisioned before payment
        assert len(await uow.subscriptions.active_for_user(user_id)) == 0
    assert len(fake.users) == 0

    # First webhook completes the payment.
    async with uow:
        moved = await payments.process(
            uow, payment_id=payment_id, status=TransactionStatus.COMPLETED
        )
        await uow.commit()
    assert moved is True
    async with uow:
        assert len(await uow.subscriptions.active_for_user(user_id)) == 1
    assert len(fake.users) == 1

    # Duplicate / late webhook is a no-op — no second subscription, no second panel user.
    async with uow:
        moved_again = await payments.process(
            uow, payment_id=payment_id, status=TransactionStatus.COMPLETED
        )
        await uow.commit()
    assert moved_again is False
    async with uow:
        assert len(await uow.subscriptions.active_for_user(user_id)) == 1
    assert len(fake.users) == 1
    assert len(bus.of_type(SubscriptionPurchased)) == 1


async def test_purchase_discount_is_consumed_on_purchase(uow: UnitOfWork, services) -> None:
    purchase, payments, _fake, _bus = services
    async with uow:
        user = await make_user(uow, purchase_discount_pct=20)
        plan, _ = await make_plan(uow, price_minor=30000)
        await uow.commit()
        user_id = user.id
        req = PurchaseRequest(
            user_id=user_id, plan_id=plan.id, duration_days=30, currency=Currency.RUB
        )
        txn, _ = await purchase.start(uow, req)
        await uow.commit()
        payment_id = txn.payment_id
    async with uow:
        await payments.process(uow, payment_id=payment_id, status=TransactionStatus.COMPLETED)
        await uow.commit()
    async with uow:
        refreshed = await uow.users.get(user_id)
        assert refreshed is not None
        assert refreshed.purchase_discount_pct == 0  # one-shot consumed
