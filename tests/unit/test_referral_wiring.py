"""Referral commission is actually issued when money enters via PaymentService.process.

Regression for the "referral reward never fires" gap: the reward is now wired into the
fulfilment path (atomic, idempotent per transaction) for both subscription payments and
balance top-ups.
"""

from __future__ import annotations

from src.application.dto.pricing import PurchaseRequest
from src.application.services.payment import PaymentService
from src.application.services.pricing import PricingService
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.enums import Currency, ReferralLevel, TransactionStatus, TransactionType
from src.infrastructure.database.models.referral import Referral
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient, RecordingEventBus


def _services() -> tuple[PurchaseService, PaymentService]:
    bus = RecordingEventBus()
    subs = SubscriptionService(RemnawaveService(FakeRemnawaveClient()))
    purchase = PurchaseService(PricingService(), subs, bus)
    return purchase, PaymentService(purchase, bus, ReferralService(bus))


async def _bind(uow: UnitOfWork, referrer_id: int, referred_id: int) -> None:
    uow.session.add(
        Referral(referrer_id=referrer_id, referred_id=referred_id, level=ReferralLevel.FIRST)
    )
    await uow.flush()


async def test_referral_commission_on_paid_subscription(uow: UnitOfWork) -> None:
    purchase, payments = _services()
    async with uow:
        referrer = await make_user(uow, telegram_id=1)
        await uow.commit()
        buyer = await make_user(uow, telegram_id=2, referred_by_id=referrer.id)
        await _bind(uow, referrer.id, buyer.id)
        plan, _ = await make_plan(uow, price_minor=30000, days=30)
        await uow.commit()
        referrer_id, buyer_id, plan_id = referrer.id, buyer.id, plan.id
        txn, _ = await purchase.start(
            uow,
            PurchaseRequest(
                user_id=buyer_id, plan_id=plan_id, duration_days=30, currency=Currency.RUB
            ),
        )
        await uow.commit()
        payment_id = txn.payment_id
    async with uow:
        await payments.process(uow, payment_id=payment_id, status=TransactionStatus.COMPLETED)
        await uow.commit()
    async with uow:
        r = await uow.users.get(referrer_id)
        assert r is not None and r.balance_minor == 7500  # 25% of 30000


async def test_referral_commission_on_deposit(uow: UnitOfWork) -> None:
    _purchase, payments = _services()
    async with uow:
        referrer = await make_user(uow, telegram_id=1)
        await uow.commit()
        buyer = await make_user(uow, telegram_id=2, referred_by_id=referrer.id)
        await _bind(uow, referrer.id, buyer.id)
        await uow.commit()
        referrer_id = referrer.id
        txn = Transaction(
            user_id=buyer.id,
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            amount_minor=10000,
            currency=Currency.RUB,
        )
        await uow.transactions.add(txn)
        await uow.commit()
        payment_id = txn.payment_id
    async with uow:
        await payments.process(uow, payment_id=payment_id, status=TransactionStatus.COMPLETED)
        await uow.commit()
    async with uow:
        r = await uow.users.get(referrer_id)
        assert r is not None and r.balance_minor == 2500  # 25% of 10000
