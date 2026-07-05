"""PricingService discount stacking (docs/context/04)."""

from __future__ import annotations

from src.application.dto.pricing import PurchaseRequest
from src.application.services.pricing import PricingService
from src.core.enums import Currency
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import add_promo_group, make_plan, make_user


async def _req(uow: UnitOfWork, **over: object) -> PurchaseRequest:
    user = await make_user(uow)
    plan, _ = await make_plan(uow, price_minor=30000, days=30)
    await uow.commit()
    return PurchaseRequest(
        user_id=user.id,
        plan_id=plan.id,
        duration_days=30,
        currency=Currency.RUB,
        **over,  # type: ignore[arg-type]
    )


async def test_base_price_no_discounts(uow: UnitOfWork) -> None:
    async with uow:
        req = await _req(uow)
        quote = await PricingService().quote(uow, req)
    assert quote.base.amount_minor == 30000
    assert quote.discount_pct == 0
    assert quote.final.amount_minor == 30000
    assert not quote.is_free


async def test_promo_group_discount_applies(uow: UnitOfWork) -> None:
    async with uow:
        user = await make_user(uow)
        plan, _ = await make_plan(uow, price_minor=20000, days=30)
        await add_promo_group(uow, user, server_discount_pct=25)
        await uow.commit()
        req = PurchaseRequest(
            user_id=user.id, plan_id=plan.id, duration_days=30, currency=Currency.RUB
        )
        quote = await PricingService().quote(uow, req)
    assert quote.discount_pct == 25
    assert quote.final.amount_minor == 15000


async def test_stacked_discount_caps_at_100_and_is_free(uow: UnitOfWork) -> None:
    async with uow:
        user = await make_user(uow, personal_discount_pct=60, purchase_discount_pct=60)
        plan, _ = await make_plan(uow, price_minor=50000, days=30)
        await uow.commit()
        req = PurchaseRequest(
            user_id=user.id, plan_id=plan.id, duration_days=30, currency=Currency.RUB
        )
        quote = await PricingService().quote(uow, req)
    assert quote.discount_pct == 100
    assert quote.final.amount_minor == 0
    assert quote.is_free
