"""PricingService — computes the final price with discount stacking (docs/context/04).

Order: base plan price (+ squad add-ons) -> effective promo-group % + period % -> personal +
one-shot purchase discount -> cap at 100%. A zero/100%-off result is a free purchase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from src.application.dto.pricing import PriceQuote, PurchaseRequest
from src.core.constants import MAX_DISCOUNT_PERCENT
from src.core.exceptions import PurchaseError
from src.core.money import Money
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice
from src.infrastructure.database.models.promo_group import PromoGroup, UserPromoGroup
from src.infrastructure.database.models.server_squad import ServerSquad

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork


class PricingService:
    async def quote(self, uow: UnitOfWork, req: PurchaseRequest) -> PriceQuote:
        plan = await uow.plans.get(req.plan_id)
        if plan is None or not plan.is_active:
            raise PurchaseError(f"plan {req.plan_id} not found or inactive")

        base_minor = await self._base_price_minor(uow, plan, req)
        squads_minor = await self._squads_addon_minor(uow, req)
        base_total = Money(base_minor + squads_minor, req.currency)

        promo_pct = await self._promo_group_discount(uow, req)
        user = await uow.users.get(req.user_id)
        personal = user.personal_discount_pct if user else 0
        purchase = user.purchase_discount_pct if user else 0

        discount_pct = min(MAX_DISCOUNT_PERCENT, promo_pct + personal + purchase)
        final = base_total.apply_discount(discount_pct)

        return PriceQuote(
            base=base_total,
            discount_pct=discount_pct,
            final=final,
            components={"plan": base_minor, "squads": squads_minor},
        )

    async def _base_price_minor(self, uow: UnitOfWork, plan: Plan, req: PurchaseRequest) -> int:
        stmt = (
            select(PlanPrice.price_minor)
            .join(PlanDuration, PlanPrice.plan_duration_id == PlanDuration.id)
            .where(
                PlanDuration.plan_id == plan.id,
                PlanDuration.days == req.duration_days,
                PlanPrice.currency == req.currency,
            )
            .limit(1)
        )
        price = await uow.session.scalar(stmt)
        if price is None:
            raise PurchaseError(
                f"no price for plan={plan.id} days={req.duration_days} {req.currency.value}"
            )
        return int(price)

    async def _squads_addon_minor(self, uow: UnitOfWork, req: PurchaseRequest) -> int:
        if not req.internal_squads:
            return 0
        stmt = select(ServerSquad.price_minor).where(
            ServerSquad.squad_uuid.in_(tuple(req.internal_squads))
        )
        prices = (await uow.session.scalars(stmt)).all()
        return sum(int(p) for p in prices)

    async def _promo_group_discount(self, uow: UnitOfWork, req: PurchaseRequest) -> int:
        """Highest-priority group the user belongs to; server % + this duration's period %."""
        stmt = (
            select(PromoGroup)
            .join(UserPromoGroup, UserPromoGroup.promo_group_id == PromoGroup.id)
            .where(UserPromoGroup.user_id == req.user_id)
            .order_by(PromoGroup.priority.desc())
            .limit(1)
        )
        group = await uow.session.scalar(stmt)
        if group is None:
            return 0
        period_pct = int(group.period_discounts.get(str(req.duration_days), 0))
        return group.server_discount_pct + period_pct
