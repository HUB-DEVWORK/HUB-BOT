"""PromoService — validate + apply a promocode, recording a per-user activation.

Wallet rewards (balance/discounts/group) mutate the user row. Panel-affecting rewards
are applied instantly too: DURATION extends the active subscription (panel-first),
SUBSCRIPTION grants a free one via the same path as the trial. Reward is applied,
then the activation is persisted (unique per user).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from src.application.dto.pricing import PurchaseRequest
from src.core.enums import Availability, Currency, PurchaseType, RewardType
from src.core.exceptions import DomainError
from src.infrastructure.database.models.promo_group import UserPromoGroup
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from src.application.services.subscription import SubscriptionService
    from src.infrastructure.database.uow import UnitOfWork

_WALLET_REWARDS = {
    RewardType.BALANCE,
    RewardType.PERSONAL_DISCOUNT,
    RewardType.PURCHASE_DISCOUNT,
    RewardType.PROMO_GROUP,
}


class PromoError(DomainError):
    """Promocode is invalid, expired, exhausted, or not applicable to this user."""


class PromoService:
    def __init__(self, subscriptions: SubscriptionService | None = None) -> None:
        self._subscriptions = subscriptions

    async def apply(self, uow: UnitOfWork, user: User, code: str) -> RewardType:
        # Lock the promocode row so the max_activations count-then-insert is atomic.
        promo = await uow.promocodes.get_by_code_for_update(code)
        if promo is None or not promo.is_active:
            raise PromoError("promocode not found or inactive")
        await self._check_validity(uow, promo, user)

        if promo.reward_type in _WALLET_REWARDS:
            await self._apply_wallet_reward(uow, promo, user)
        elif promo.reward_type in (RewardType.DURATION, RewardType.SUBSCRIPTION):
            await self._apply_subscription_reward(uow, promo, user)
        else:
            # traffic/devices: not instantly applicable — surface a clear message.
            raise PromoError(f"reward {promo.reward_type.value} is not supported yet")

        await uow.promocode_activations.add(
            PromocodeActivation(promocode_id=promo.id, user_id=user.id)
        )
        return promo.reward_type

    async def _check_validity(self, uow: UnitOfWork, promo: Promocode, user: User) -> None:
        now = dt.datetime.now(dt.UTC)
        if promo.expires_at is not None and promo.expires_at < now:
            raise PromoError("promocode expired")
        if await uow.promocode_activations.is_activated_by(promo.id, user.id):
            raise PromoError("promocode already activated by this user")
        if promo.max_activations is not None:
            used = await uow.promocode_activations.count(promocode_id=promo.id)
            if used >= promo.max_activations:
                raise PromoError("promocode activation limit reached")
        if promo.availability is Availability.NEW and user.has_had_paid_subscription:
            raise PromoError("promocode is for new users only")
        if promo.availability is Availability.EXISTING and not user.has_had_paid_subscription:
            raise PromoError("promocode is for existing customers only")
        if promo.availability is Availability.INVITED and user.referred_by_id is None:
            raise PromoError("promocode is for invited (referred) users only")

    async def _apply_subscription_reward(
        self, uow: UnitOfWork, promo: Promocode, user: User
    ) -> None:
        """DURATION: +N days to the active sub. SUBSCRIPTION: grant a free N-day sub."""
        if self._subscriptions is None:
            raise PromoError("subscription rewards are not available")
        days = int(promo.reward_value or 0)
        if days <= 0:
            raise PromoError("promocode has no duration configured")

        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
        if promo.reward_type is RewardType.DURATION:
            if sub is None or not sub.status.is_usable:
                raise PromoError("активная подписка нужна для этого промокода")
            await self._subscriptions.renew(uow, sub, days=days, telegram_id=user.telegram_id)
            return

        # SUBSCRIPTION: free days — extend if a subscription exists, else grant one
        # through the same path as the trial (panel-first, snapshot frozen).
        if sub is not None and sub.status.is_usable:
            await self._subscriptions.renew(uow, sub, days=days, telegram_id=user.telegram_id)
            return
        plan = await uow.plans.find_one(is_trial=True) or await uow.plans.find_one(name="Trial")
        if plan is None:
            from src.infrastructure.database.models.plan import Plan

            plan = Plan(public_code="trial", name="Trial", is_trial=True, is_active=False)
            await uow.plans.add(plan)
        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan.id,
            duration_days=days,
            currency=Currency.RUB,
            purchase_type=PurchaseType.NEW,
        )
        await self._subscriptions.grant(uow, user=user, plan=plan, req=req, is_trial=False)

    async def _apply_wallet_reward(self, uow: UnitOfWork, promo: Promocode, user: User) -> None:
        match promo.reward_type:
            case RewardType.BALANCE:
                await uow.users.increment_balance(user, promo.reward_value)  # atomic
            case RewardType.PERSONAL_DISCOUNT:
                user.personal_discount_pct = promo.reward_value
            case RewardType.PURCHASE_DISCOUNT:
                user.purchase_discount_pct = promo.reward_value
            case RewardType.PROMO_GROUP if promo.promo_group_id is not None:
                uow.session.add(
                    UserPromoGroup(user_id=user.id, promo_group_id=promo.promo_group_id)
                )
            case _:
                raise PromoError("unsupported wallet reward")
