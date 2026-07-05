"""SubscriptionService — provisions and mutates subscriptions (panel-first, ADR-0005)."""

from __future__ import annotations

import datetime as dt
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
    ) -> Subscription:
        """Create a brand-new subscription: panel user first, then persist locally."""
        short_id = generate_short_id()
        now = dt.datetime.now(dt.UTC)
        expire_at = now + dt.timedelta(days=req.duration_days)
        traffic_bytes = plan.traffic_limit_bytes or 0

        spec = self._remnawave.build_spec(
            short_id=short_id,
            telegram_id=user.telegram_id,
            expire_at=expire_at,
            traffic_limit_bytes=traffic_bytes,
            device_limit=plan.device_limit,
            internal_squads=req.internal_squads or tuple(plan.internal_squads),
            external_squad=req.external_squad or plan.external_squad,
        )
        # Panel-first: create the panel user OUTSIDE any assumption of a local commit.
        panel_user = await self._remnawave.provision(spec)

        subscription = Subscription(
            user_id=user.id,
            remnawave_uuid=panel_user.uuid,
            short_id=short_id,
            plan_id=plan.id,
            plan_snapshot=_plan_snapshot(plan),
            status=SubscriptionStatus.TRIAL if is_trial else SubscriptionStatus.ACTIVE,
            is_trial=is_trial,
            traffic_limit_bytes=traffic_bytes,
            device_limit=plan.device_limit,
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
        else:
            user.has_had_paid_subscription = True
        return subscription

    async def renew(
        self, uow: UnitOfWork, subscription: Subscription, *, days: int
    ) -> Subscription:
        """Extend an existing subscription and push the new expiry to the panel."""
        if subscription.remnawave_uuid is None:
            raise PurchaseError("cannot renew a subscription with no panel user")
        base = subscription.expire_at or dt.datetime.now(dt.UTC)
        subscription.expire_at = max(base, dt.datetime.now(dt.UTC)) + dt.timedelta(days=days)
        subscription.status = SubscriptionStatus.ACTIVE
        spec = self._remnawave.build_spec(
            short_id=subscription.short_id,
            telegram_id=subscription.user.telegram_id if subscription.user else None,
            expire_at=subscription.expire_at,
            traffic_limit_bytes=subscription.traffic_limit_bytes,
            device_limit=subscription.device_limit,
            internal_squads=tuple(subscription.internal_squads),
            external_squad=subscription.external_squad,
        )
        await self._remnawave.apply(subscription.remnawave_uuid, spec)
        return subscription

    @staticmethod
    def apply_purchase_discount_reset(user: User, purchase_type: PurchaseType) -> None:
        """One-shot ``purchase_discount`` is consumed on any paid purchase (gotcha #14)."""
        if purchase_type in (PurchaseType.NEW, PurchaseType.RENEW, PurchaseType.CHANGE):
            user.purchase_discount_pct = 0
