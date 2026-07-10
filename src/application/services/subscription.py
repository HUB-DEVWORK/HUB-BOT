"""SubscriptionService — provisions and mutates subscriptions (panel-first, ADR-0005)."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.application.dto.pricing import PurchaseRequest
from src.application.services.ids import generate_short_id
from src.application.services.remnawave import RemnawaveService
from src.core.enums import PurchaseType, SubscriptionStatus
from src.core.exceptions import PurchaseError
from src.infrastructure.database.models.plan import Plan
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork


def _plan_snapshot(plan: Plan) -> dict[str, Any]:
    """Freeze the plan shape at purchase time (gotcha #8)."""
    return {
        "plan_id": plan.id,
        "name": plan.name,
        "type": plan.type.value,
        "traffic_limit_bytes": plan.traffic_limit_bytes,
        "device_limit": plan.device_limit,
        "traffic_limit_strategy": plan.traffic_limit_strategy,
        "internal_squads": list(plan.internal_squads),
        "external_squad": plan.external_squad,
    }


class SubscriptionService:
    def __init__(self, remnawave: RemnawaveService) -> None:
        self._remnawave = remnawave

    async def grant(
        self,
        uow: UnitOfWork,
        *,
        user: User,
        plan: Plan,
        req: PurchaseRequest,
        is_trial: bool = False,
        mark_paid: bool = True,
    ) -> Subscription:
        """Create a brand-new subscription: panel user first, then persist locally.

        ``mark_paid`` records that the user became a paying customer. A free promo grant passes
        False — a gift is neither a trial nor a purchase, so it must not flip has_had_paid (which
        gates NEW/EXISTING-only promos and feeds paid-conversion analytics)."""
        short_id = generate_short_id()
        now = dt.datetime.now(dt.UTC)
        expire_at = now + dt.timedelta(days=req.duration_days)
        # Constructor purchases override the (service) plan's limits with the picked pack.
        traffic_bytes = (
            req.traffic_limit_bytes
            if req.traffic_limit_bytes is not None
            else plan.traffic_limit_bytes or 0
        )
        device_limit = req.device_limit if req.device_limit is not None else plan.device_limit

        spec = self._remnawave.build_spec(
            short_id=short_id,
            telegram_id=user.telegram_id,
            expire_at=expire_at,
            traffic_limit_bytes=traffic_bytes,
            device_limit=device_limit,
            internal_squads=req.internal_squads or tuple(plan.internal_squads),
            external_squad=req.external_squad or plan.external_squad,
        )
        # Panel-first: create the panel user OUTSIDE any assumption of a local commit.
        panel_user = await self._remnawave.provision(spec)

        snapshot = _plan_snapshot(plan)
        snapshot["traffic_limit_bytes"] = traffic_bytes or None
        snapshot["device_limit"] = device_limit
        subscription = Subscription(
            user_id=user.id,
            remnawave_uuid=panel_user.uuid,
            short_id=short_id,
            plan_id=plan.id,
            plan_snapshot=snapshot,
            status=SubscriptionStatus.TRIAL if is_trial else SubscriptionStatus.ACTIVE,
            is_trial=is_trial,
            traffic_limit_bytes=traffic_bytes,
            device_limit=device_limit,
            internal_squads=list(spec.internal_squads),
            external_squad=spec.external_squad,
            start_at=now,
            expire_at=expire_at,
            subscription_url=panel_user.subscription_url,
        )
        await uow.subscriptions.add(subscription)

        user.current_subscription_id = subscription.id
        if is_trial:
            user.is_trial_available = False
        elif mark_paid:
            user.has_had_paid_subscription = True
        return subscription

    async def renew(
        self,
        uow: UnitOfWork,
        subscription: Subscription,
        *,
        days: int,
        telegram_id: int | None = None,
    ) -> Subscription:
        """Extend an existing subscription and push the new expiry to the panel.

        ``telegram_id`` is passed in explicitly (not read from ``subscription.user``) to avoid
        an async lazy-load on the relationship.
        """
        if subscription.remnawave_uuid is None:
            raise PurchaseError("cannot renew a subscription with no panel user")
        base = subscription.expire_at or dt.datetime.now(dt.UTC)
        subscription.expire_at = max(base, dt.datetime.now(dt.UTC)) + dt.timedelta(days=days)
        subscription.status = SubscriptionStatus.ACTIVE
        spec = self._remnawave.build_spec(
            short_id=subscription.short_id,
            telegram_id=telegram_id,
            expire_at=subscription.expire_at,
            traffic_limit_bytes=subscription.traffic_limit_bytes,
            device_limit=subscription.device_limit,
            internal_squads=tuple(subscription.internal_squads or ()),
            external_squad=subscription.external_squad,
        )
        await self._remnawave.apply(subscription.remnawave_uuid, spec)
        return subscription

    async def change(
        self,
        uow: UnitOfWork,
        subscription: Subscription,
        *,
        user: User,
        plan: Plan,
        req: PurchaseRequest,
        carryover_trial: bool = False,
    ) -> Subscription:
        """Switch the subscription to another plan: same panel user, new period from now.

        A PAID old period's remainder is already monetized as a price credit
        (PricingService); a TRIAL remainder is carried over as bonus days when
        ``carryover_trial`` is set. Panel-first like every other write.
        """
        if subscription.remnawave_uuid is None:
            raise PurchaseError("cannot change a subscription with no panel user")
        bonus = 0
        if carryover_trial and subscription.is_trial and subscription.expire_at is not None:
            remaining = (subscription.expire_at - dt.datetime.now(dt.UTC)).total_seconds()
            bonus = max(0, math.ceil(remaining / 86400))  # don't lose a partial day
        subscription.plan_id = plan.id
        subscription.plan_snapshot = _plan_snapshot(plan)
        subscription.is_trial = False
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.traffic_limit_bytes = (
            req.traffic_limit_bytes
            if req.traffic_limit_bytes is not None
            else (plan.traffic_limit_bytes or 0)
        )
        subscription.device_limit = (
            req.device_limit if req.device_limit is not None else plan.device_limit
        )
        if req.internal_squads:
            subscription.internal_squads = list(req.internal_squads)
        elif plan.internal_squads:
            subscription.internal_squads = list(plan.internal_squads)
        # Carry the new plan's external squad too — otherwise a plan change moving the user to a
        # different exit silently keeps the old one (build_spec reads the sub, not the plan).
        if req.external_squad is not None:
            subscription.external_squad = req.external_squad
        elif plan.external_squad is not None:
            subscription.external_squad = plan.external_squad
        subscription.expire_at = dt.datetime.now(dt.UTC) + dt.timedelta(
            days=req.duration_days + bonus
        )
        spec = self._remnawave.build_spec(
            short_id=subscription.short_id,
            telegram_id=user.telegram_id,
            expire_at=subscription.expire_at,
            traffic_limit_bytes=subscription.traffic_limit_bytes,
            device_limit=subscription.device_limit,
            internal_squads=tuple(subscription.internal_squads or ()),
            external_squad=subscription.external_squad,
        )
        # On a plan CHANGE the new plan's device cap / exit are authoritative: if it grants
        # unlimited devices or has no external squad, the old panel value must be CLEARED, not
        # left in place. create/renew keep the omit-semantics — only CHANGE opts into the clear.
        spec = replace(spec, reset_device_limit=True, reset_external_squad=True)
        await self._remnawave.apply(subscription.remnawave_uuid, spec)
        return subscription

    async def push_limits(
        self, uow: UnitOfWork, subscription: Subscription, *, telegram_id: int | None = None
    ) -> Subscription:
        """Push the subscription's current limits to the panel without touching expiry."""
        if subscription.remnawave_uuid is None:
            raise PurchaseError("subscription has no panel user")
        spec = self._remnawave.build_spec(
            short_id=subscription.short_id,
            telegram_id=telegram_id,
            expire_at=subscription.expire_at or dt.datetime.now(dt.UTC),
            traffic_limit_bytes=subscription.traffic_limit_bytes,
            device_limit=subscription.device_limit,
            internal_squads=tuple(subscription.internal_squads or ()),
            external_squad=subscription.external_squad,
        )
        await self._remnawave.apply(subscription.remnawave_uuid, spec)
        return subscription

    @staticmethod
    def apply_purchase_discount_reset(user: User, purchase_type: PurchaseType) -> None:
        """One-shot ``purchase_discount`` is consumed on ANY paid purchase (gotcha #14) —
        including a traffic top-up, whose price is also reduced by it (#9)."""
        if purchase_type in (
            PurchaseType.NEW,
            PurchaseType.RENEW,
            PurchaseType.CHANGE,
            PurchaseType.TRAFFIC_TOPUP,
        ):
            user.purchase_discount_pct = 0
