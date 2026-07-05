"""Async helpers to seed rows in tests. Call inside an open ``async with uow:`` block."""

from __future__ import annotations

from src.application.services.ids import generate_referral_code
from src.core.enums import Currency
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice
from src.infrastructure.database.models.promo_group import PromoGroup, UserPromoGroup
from src.infrastructure.database.models.user import User
from src.infrastructure.database.uow import UnitOfWork


async def make_user(uow: UnitOfWork, *, telegram_id: int = 111, **overrides: object) -> User:
    user = User(
        telegram_id=telegram_id,
        referral_code=generate_referral_code(),
        currency=Currency.RUB,
        **overrides,  # type: ignore[arg-type]
    )
    await uow.users.add(user)
    return user


async def make_plan(
    uow: UnitOfWork,
    *,
    price_minor: int = 30000,
    currency: Currency = Currency.RUB,
    days: int = 30,
    code: str = "base",
) -> tuple[Plan, PlanDuration]:
    plan = Plan(public_code=code, name=f"Plan {code}")
    await uow.plans.add(plan)
    duration = PlanDuration(plan_id=plan.id, days=days)
    uow.session.add(duration)
    await uow.flush()
    uow.session.add(
        PlanPrice(plan_duration_id=duration.id, currency=currency, price_minor=price_minor)
    )
    await uow.flush()
    return plan, duration


async def add_promo_group(
    uow: UnitOfWork, user: User, *, server_discount_pct: int, priority: int = 10
) -> PromoGroup:
    group = PromoGroup(
        name=f"group-{priority}", priority=priority, server_discount_pct=server_discount_pct
    )
    uow.session.add(group)
    await uow.flush()
    uow.session.add(UserPromoGroup(user_id=user.id, promo_group_id=group.id))
    await uow.flush()
    return group
