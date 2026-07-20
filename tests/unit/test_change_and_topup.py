"""Plan change (proration credit, same panel user) and traffic top-up."""

from __future__ import annotations

import datetime as dt

import pytest

from src.application.dto.pricing import PurchaseRequest
from src.application.services.payment import PaymentService
from src.application.services.pricing import PricingService
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.constants import BYTES_PER_GB
from src.core.enums import Currency, PurchaseType, TransactionStatus
from src.infrastructure.database.models.constructor import TrafficPack
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient, RecordingEventBus


def _services() -> tuple[PurchaseService, FakeRemnawaveClient]:
    fake = FakeRemnawaveClient()
    bus = RecordingEventBus()
    subs = SubscriptionService(RemnawaveService(fake))
    purchase = PurchaseService(PricingService(), subs, bus)
    PaymentService(purchase, bus, ReferralService(bus))
    return purchase, fake


async def _buy(
    purchase: PurchaseService, uow: UnitOfWork, user_id: int, plan_id: int, days: int
) -> None:
    req = PurchaseRequest(
        user_id=user_id, plan_id=plan_id, duration_days=days, currency=Currency.RUB
    )
    txn, _ = await purchase.start(uow, req)
    await uow.transactions.transition_status(
        txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
    )
    await purchase.fulfill(uow, txn)
    await uow.commit()


async def test_change_prorates_and_keeps_panel_user(uow: UnitOfWork) -> None:
    purchase, fake = _services()
    async with uow:
        user = await make_user(uow, balance_minor=100000)
        plan_a, _ = await make_plan(uow, price_minor=30000)  # 300 ₽ / 30 дн.
        plan_b, _ = await make_plan(uow, public_code="premium", name="Premium", price_minor=60000)
        await uow.commit()
        await _buy(purchase, uow, user.id, plan_a.id, 30)

        sub = (await uow.subscriptions.active_for_user(user.id))[0]
        old_uuid, old_short = sub.remnawave_uuid, sub.short_id
        assert len(fake.users) == 1

        # A different plan while the sub is usable resolves to CHANGE.
        ptype, sub_id = await purchase.resolve_purchase_type(uow, user.id, plan_b.id)
        assert ptype is PurchaseType.CHANGE and sub_id == sub.id

        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan_b.id,
            duration_days=30,
            currency=Currency.RUB,
            purchase_type=PurchaseType.CHANGE,
            subscription_id=sub.id,
        )
        quote = await purchase._pricing.quote(uow, req)
        # Proration: pay the FULL 600 ₽ list price of the new period (no discount credit); the
        # remaining 300 ₽ of plan A carries over as bonus days on plan B (600 ₽/30 дн.):
        # 300 ₽ buys 15 days at plan B's rate.
        assert "change_credit" not in quote.components
        assert quote.final.amount_minor == 60000
        assert 14 <= quote.components["change_bonus_days"] <= 15

        txn, _ = await purchase.start(uow, req)
        await uow.transactions.transition_status(
            txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
        )
        await purchase.fulfill(uow, txn)
        await uow.commit()

        # Same subscription row and SAME panel user — no orphan, no reconnect needed.
        assert len(fake.users) == 1
        assert sub.remnawave_uuid == old_uuid and sub.short_id == old_short
        assert sub.plan_id == plan_b.id
        assert (sub.plan_snapshot or {}).get("name") == "Premium"
        assert sub.expire_at is not None
        left = (sub.expire_at - dt.datetime.now(dt.UTC)).days
        assert 44 <= left <= 45  # 30 purchased + ~15 carried over from plan A's remainder


async def test_change_credit_zero_for_missing_subscription(uow: UnitOfWork) -> None:
    pricing = PricingService()
    async with uow:
        assert await pricing._change_credit(uow, 99999) == 0


async def test_traffic_topup_adds_bytes_and_pushes_panel(uow: UnitOfWork) -> None:
    purchase, fake = _services()
    async with uow:
        user = await make_user(uow, balance_minor=100000)
        plan, _ = await make_plan(uow, price_minor=30000, traffic_limit_bytes=50 * BYTES_PER_GB)
        uow.session.add(TrafficPack(gb=20, price_minor=5000))
        await uow.commit()
        await _buy(purchase, uow, user.id, plan.id, 30)

        sub = (await uow.subscriptions.active_for_user(user.id))[0]
        before = sub.traffic_limit_bytes
        pack = await uow.traffic_packs.find_one(gb=20)
        assert pack is not None

        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan.id,
            duration_days=0,
            currency=Currency.RUB,
            purchase_type=PurchaseType.TRAFFIC_TOPUP,
            subscription_id=sub.id,
            traffic_pack_id=pack.id,
        )
        quote = await purchase._pricing.quote(uow, req)
        assert quote.final.amount_minor == 5000

        txn, _ = await purchase.start(uow, req)
        assert (txn.plan_snapshot or {}).get("name") == "+20 ГБ трафика"
        await uow.transactions.transition_status(
            txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
        )
        await purchase.fulfill(uow, txn)
        await uow.commit()

        assert sub.traffic_limit_bytes == before + 20 * BYTES_PER_GB
        # expiry untouched, same panel user
        assert len(fake.users) == 1


async def test_topup_rejected_for_unlimited(uow: UnitOfWork) -> None:
    purchase, _fake = _services()
    async with uow:
        user = await make_user(uow, balance_minor=100000)
        plan, _ = await make_plan(uow, price_minor=30000)  # unlimited traffic
        uow.session.add(TrafficPack(gb=20, price_minor=5000))
        await uow.commit()
        await _buy(purchase, uow, user.id, plan.id, 30)
        sub = (await uow.subscriptions.active_for_user(user.id))[0]
        pack = await uow.traffic_packs.find_one(gb=20)
        assert pack is not None

        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan.id,
            duration_days=0,
            currency=Currency.RUB,
            purchase_type=PurchaseType.TRAFFIC_TOPUP,
            subscription_id=sub.id,
            traffic_pack_id=pack.id,
        )
        txn, _ = await purchase.start(uow, req)
        await uow.transactions.transition_status(
            txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
        )
        from src.core.exceptions import PurchaseError

        with pytest.raises(PurchaseError):
            await purchase.fulfill(uow, txn)
