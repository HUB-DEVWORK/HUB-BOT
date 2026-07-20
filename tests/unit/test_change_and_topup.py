"""Plan change (proration credit, same panel user) and traffic top-up."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from src.application.dto.pricing import PurchaseRequest
from src.application.services.payment import PaymentService
from src.application.services.pricing import PricingService
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.constants import BYTES_PER_GB
from src.core.enums import Currency, PurchaseType, SubscriptionStatus, TransactionStatus
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


async def _change(
    purchase: PurchaseService, uow: UnitOfWork, user_id: int, plan_id: int, days: int
) -> tuple[Any, Any]:
    """Buy a DIFFERENT/same plan on an existing sub (auto-resolves RENEW/CHANGE); returns
    (purchase_type, quote)."""
    ptype, sub_id = await purchase.resolve_purchase_type(uow, user_id, plan_id)
    req = PurchaseRequest(
        user_id=user_id,
        plan_id=plan_id,
        duration_days=days,
        currency=Currency.RUB,
        purchase_type=ptype,
        subscription_id=sub_id,
    )
    quote = await purchase._pricing.quote(uow, req)
    txn, _ = await purchase.start(uow, req)
    await uow.transactions.transition_status(
        txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
    )
    await purchase.fulfill(uow, txn)
    await uow.commit()
    return ptype, quote


async def test_user_reported_plan_change_scenarios(uow: UnitOfWork) -> None:
    """Proof for every case in the bug report: switching/renewing NEVER resets the term, and the
    price is the full list price of the new period (unused value carries over as bonus days)."""
    purchase, _ = _services()
    async with uow:
        user = await make_user(uow, balance_minor=100_000_000)
        a, _ = await make_plan(uow, price_minor=30000, days=30, public_code="A", name="A")  # 300₽
        a2, _ = await make_plan(
            uow, price_minor=30000, days=30, public_code="A2", name="A2"
        )  # 300₽
        b, _ = await make_plan(uow, price_minor=60000, days=30, public_code="B", name="B")  # 600₽
        c, _ = await make_plan(uow, price_minor=15000, days=30, public_code="C", name="C")  # 150₽
        await uow.commit()

        def days_left(s: object) -> int:
            return round((s.expire_at - dt.datetime.now(dt.UTC)).total_seconds() / 86400)  # type: ignore[attr-defined]

        async def fresh_sub_with(days_remaining: int) -> object:
            # A clean 30-day A purchase, then force the remaining days to the scenario's value.
            for s in await uow.subscriptions.active_for_user(user.id):
                s.status = SubscriptionStatus.DELETED
            user.current_subscription_id = None
            await uow.commit()
            await _buy(purchase, uow, user.id, a.id, 30)
            s = (await uow.subscriptions.active_for_user(user.id))[0]
            s.expire_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=days_remaining)
            await uow.commit()
            return s

        # [1] 60 дней A -> переход на дороже B(30д): НЕ сброс до 30, цена ПОЛНАЯ 600₽.
        s = await fresh_sub_with(60)
        ptype, q = await _change(purchase, uow, user.id, b.id, 30)
        s = await uow.subscriptions.get(s.id)  # type: ignore[attr-defined]
        print(
            f"\n[1] 60д A → B(30д): {ptype.value} цена={q.final.amount_minor // 100}₽ "
            f"бонус={q.components.get('change_bonus_days')}д срок={days_left(s)}д (было бы 30)"
        )
        assert ptype is PurchaseType.CHANGE
        assert q.final.amount_minor == 60000  # полная цена B, без «кредита»
        assert 59 <= days_left(s) <= 60  # 30 куплено + 30 бонус; НЕ сброс до 30
        assert s.plan_id == b.id  # type: ignore[attr-defined]

        # [2] 60 дней A -> переход на дешевле C(30д): цена ПОЛНАЯ 150₽, остаток даёт больше дней.
        s = await fresh_sub_with(60)
        ptype, q = await _change(purchase, uow, user.id, c.id, 30)
        s = await uow.subscriptions.get(s.id)  # type: ignore[attr-defined]
        print(
            f"[2] 60д A → C(30д, дешевле): {ptype.value} цена={q.final.amount_minor // 100}₽ "
            f"бонус={q.components.get('change_bonus_days')}д срок={days_left(s)}д"
        )
        assert q.final.amount_minor == 15000  # полная цена C (никакой «доплаты»)
        assert days_left(s) >= 120  # остаток 600₽ по цене C = много дней

        # [3] 10 дней A -> ПРОДЛЕНИЕ тем же тарифом(30д): срок 40 (10+30), цена полная 300₽.
        s = await fresh_sub_with(10)
        ptype, q = await _change(purchase, uow, user.id, a.id, 30)
        s = await uow.subscriptions.get(s.id)  # type: ignore[attr-defined]
        print(
            f"[3] 10д A → продление A(30д): {ptype.value} цена={q.final.amount_minor // 100}₽ "
            f"срок={days_left(s)}д (должно 40, не 30)"
        )
        assert ptype is PurchaseType.RENEW
        assert q.final.amount_minor == 30000  # полная цена, не «за 20 дней»
        assert 39 <= days_left(s) <= 40  # 10 + 30 = 40, НЕ сброс до 30

        # [4] 10 дней A -> смена на равный по цене A2(30д): срок 40, цена полная 300₽.
        s = await fresh_sub_with(10)
        ptype, q = await _change(purchase, uow, user.id, a2.id, 30)
        s = await uow.subscriptions.get(s.id)  # type: ignore[attr-defined]
        print(
            f"[4] 10д A → смена на A2(30д, та же цена): {ptype.value} цена={q.final.amount_minor // 100}₽ "
            f"бонус={q.components.get('change_bonus_days')}д срок={days_left(s)}д"
        )
        assert ptype is PurchaseType.CHANGE
        assert q.final.amount_minor == 30000
        assert 39 <= days_left(s) <= 40  # 10 остаток -> 10 бонус + 30 куплено = 40


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
