"""SaleCampaign: active-window + monthly quota, and its effect on the price quote."""

from __future__ import annotations

import datetime as dt

from src.application.dto.pricing import PurchaseRequest
from src.application.services.pricing import PricingService
from src.core.enums import Currency
from src.infrastructure.database.models.sale_campaign import SaleCampaign
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user


async def _req(uow: UnitOfWork) -> PurchaseRequest:
    user = await make_user(uow)
    plan, _ = await make_plan(uow)  # 30000 minor for 30 days RUB
    await uow.commit()
    return PurchaseRequest(
        user_id=user.id, plan_id=plan.id, duration_days=30, currency=Currency.RUB
    )


async def test_active_sale_stacks_into_quote(uow: UnitOfWork) -> None:
    async with uow:
        uow.session.add(SaleCampaign(discount_pct=20, start_day=1, end_day=31, enabled=True))
        req = await _req(uow)
        quote = await PricingService().quote(uow, req)
    assert quote.discount_pct == 20
    assert quote.final.amount_minor == 24000
    assert quote.sale_campaign_id is not None


async def test_disabled_sale_ignored(uow: UnitOfWork) -> None:
    async with uow:
        uow.session.add(SaleCampaign(discount_pct=20, start_day=1, end_day=31, enabled=False))
        req = await _req(uow)
        quote = await PricingService().quote(uow, req)
    assert quote.discount_pct == 0
    assert quote.sale_campaign_id is None


async def test_exhausted_monthly_quota_ignored(uow: UnitOfWork) -> None:
    period = dt.datetime.now(dt.UTC).strftime("%Y-%m")
    async with uow:
        uow.session.add(
            SaleCampaign(
                discount_pct=20,
                start_day=1,
                end_day=31,
                enabled=True,
                max_uses=1,
                used_count=1,
                used_period=period,
            )
        )
        req = await _req(uow)
        quote = await PricingService().quote(uow, req)
    assert quote.discount_pct == 0  # this month's single slot is spent


async def test_consume_resets_counter_on_month_rollover(uow: UnitOfWork) -> None:
    async with uow:
        sale = SaleCampaign(
            discount_pct=20, max_uses=5, used_count=5, used_period="2000-01", enabled=True
        )
        uow.session.add(sale)
        await uow.commit()
        await uow.sales.consume(sale.id, dt.datetime(2026, 7, 8, tzinfo=dt.UTC))
    assert sale.used_period == "2026-07"
    assert sale.used_count == 1
