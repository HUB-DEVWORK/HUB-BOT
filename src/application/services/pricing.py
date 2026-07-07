"""PricingService — computes the final price with discount stacking (docs/context/04).

Order: base plan price (+ squad add-ons) -> effective promo-group % + period % -> personal +
one-shot purchase discount -> cap at 100%. A zero/100%-off result is a free purchase.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.application.dto.pricing import PriceQuote, PurchaseRequest
from src.core.constants import MAX_DISCOUNT_PERCENT
from src.core.enums import PurchaseType, TransactionStatus, TransactionType
from src.core.exceptions import PurchaseError
from src.core.money import Money
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice
from src.infrastructure.database.models.promo_group import PromoGroup, UserPromoGroup

if TYPE_CHECKING:
    from src.infrastructure.database.models.constructor import ConstructorPeriod, TrafficPack
    from src.infrastructure.database.uow import UnitOfWork


class PricingService:
    async def quote(self, uow: UnitOfWork, req: PurchaseRequest) -> PriceQuote:
        if req.purchase_type is PurchaseType.TRAFFIC_TOPUP:
            return await self._topup_quote(uow, req)
        if req.constructor_period_id is not None:
            # Constructor mode: price = period + traffic pack. The hidden service plan is
            # intentionally inactive, so the plan-catalogue checks below don't apply.
            period, pack = await self.resolve_constructor(uow, req)
            components = {"period": period.price_minor, "pack": pack.price_minor}
            base_total = Money(period.price_minor + pack.price_minor, req.currency)
        else:
            plan = await uow.plans.get(req.plan_id)
            if plan is None or not plan.is_active:
                raise PurchaseError(f"plan {req.plan_id} not found or inactive")

            base_minor = await self._base_price_minor(uow, plan, req)
            squads_minor = await self._squads_addon_minor(uow, req)
            components = {"plan": base_minor, "squads": squads_minor}
            base_total = Money(base_minor + squads_minor, req.currency)

        promo_pct = await self._promo_group_discount(uow, req)
        user = await uow.users.get(req.user_id)
        personal = user.personal_discount_pct if user else 0
        purchase = user.purchase_discount_pct if user else 0
        sale_pct, sale_id = await self._sale_discount(uow)

        discount_pct = min(MAX_DISCOUNT_PERCENT, promo_pct + personal + purchase + sale_pct)
        final = base_total.apply_discount(discount_pct)

        if req.purchase_type is PurchaseType.CHANGE and req.subscription_id is not None:
            # Plan switch: unused days of the current period become a price credit,
            # valued at what the user ACTUALLY paid (frozen txn amount), not list price.
            credit = await self._change_credit(uow, req.subscription_id)
            credit = min(credit, final.amount_minor)
            if credit > 0:
                components["change_credit"] = -credit
                final = Money(final.amount_minor - credit, req.currency)

        return PriceQuote(
            base=base_total,
            discount_pct=discount_pct,
            final=final,
            components=components,
            sale_campaign_id=sale_id if sale_pct > 0 else None,
        )

    async def _sale_discount(self, uow: UnitOfWork) -> tuple[int, int | None]:
        """Best active limited-quantity sale: (discount_pct, campaign_id) or (0, None)."""
        sale = await uow.sales.active_now(dt.datetime.now(dt.UTC))
        return (sale.discount_pct, sale.id) if sale else (0, None)

    async def _topup_quote(self, uow: UnitOfWork, req: PurchaseRequest) -> PriceQuote:
        """Traffic top-up: the pack price with the user's discounts applied."""
        pack = (
            await uow.traffic_packs.get(req.traffic_pack_id)
            if req.traffic_pack_id is not None
            else None
        )
        if pack is None or not pack.is_active or pack.gb <= 0:
            raise PurchaseError("traffic pack not found or inactive")
        user = await uow.users.get(req.user_id)
        personal = user.personal_discount_pct if user else 0
        purchase = user.purchase_discount_pct if user else 0
        discount_pct = min(MAX_DISCOUNT_PERCENT, personal + purchase)
        base = Money(pack.price_minor, req.currency)
        return PriceQuote(
            base=base,
            discount_pct=discount_pct,
            final=base.apply_discount(discount_pct),
            components={"pack": pack.price_minor},
        )

    async def _change_credit(self, uow: UnitOfWork, subscription_id: int) -> int:
        """Remaining value of the current period in minor units (0 when unknown).

        remaining_days x (paid_amount / paid_days) from the LAST completed subscription
        payment that provisioned this subscription. Trials/imports without a matching
        payment yield no credit.
        """
        sub = await uow.subscriptions.get(subscription_id)
        if sub is None or sub.expire_at is None or sub.is_trial:
            return 0
        now = dt.datetime.now(dt.UTC)
        remaining = (sub.expire_at - now).total_seconds() / 86400
        if remaining <= 0:
            return 0
        txns = await uow.transactions.list_recent(sub.user_id, limit=50)
        for txn in txns:
            pricing = txn.pricing or {}
            linked = pricing.get("subscription_id") == subscription_id or (
                # historical/imported payments predate the subscription_id link
                pricing.get("subscription_id") is None and pricing.get("plan_id") == sub.plan_id
            )
            if (
                txn.type is TransactionType.SUBSCRIPTION_PAYMENT
                and txn.status is TransactionStatus.COMPLETED
                and txn.amount_minor > 0
                and linked
            ):
                paid_days = int(pricing.get("duration_days") or 0)
                if paid_days > 0:
                    return int(txn.amount_minor * min(remaining, paid_days) / paid_days)
        return 0

    async def resolve_constructor(
        self, uow: UnitOfWork, req: PurchaseRequest
    ) -> tuple[ConstructorPeriod, TrafficPack]:
        """Load and validate the constructor selection referenced by the request."""
        period = (
            await uow.constructor_periods.get(req.constructor_period_id)
            if req.constructor_period_id is not None
            else None
        )
        if period is None or not period.is_active:
            raise PurchaseError(f"constructor period {req.constructor_period_id} unavailable")
        pack = (
            await uow.traffic_packs.get(req.traffic_pack_id)
            if req.traffic_pack_id is not None
            else None
        )
        if pack is None or not pack.is_active:
            raise PurchaseError(f"traffic pack {req.traffic_pack_id} unavailable")
        if req.duration_days != period.days:
            raise PurchaseError("constructor request duration does not match the period")
        return period, pack

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
        # ServerSquad.price_minor is a single, currency-less column, so summing it into a
        # currency-specific base would mix currencies (e.g. RUB kopeks into a USD-cent base).
        # Squad add-on pricing is intentionally disabled until per-currency squad prices exist
        # (a plan_prices-style table). Returning 0 keeps charges correct; do NOT sum raw prices.
        return 0

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
