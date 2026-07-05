"""PromoService — validate + apply a promocode, recording a per-user activation.

Panel-affecting rewards (duration/traffic/devices/subscription) are applied through the
subscription flow; this service handles the wallet/discount/group rewards and the activation
ledger. Reward is applied, then the activation is persisted (unique per user).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from src.core.enums import Availability, RewardType
from src.core.exceptions import DomainError
from src.infrastructure.database.models.promo_group import UserPromoGroup
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
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
    async def apply(self, uow: UnitOfWork, user: User, code: str) -> RewardType:
        promo = await uow.promocodes.get_by_code(code)
        if promo is None or not promo.is_active:
            raise PromoError("promocode not found or inactive")
        await self._check_validity(uow, promo, user)

        if promo.reward_type in _WALLET_REWARDS:
            await self._apply_wallet_reward(uow, promo, user)
        else:
            # duration/traffic/devices/subscription: applied by the subscription flow.
            raise PromoError(f"reward {promo.reward_type.value} must be applied during purchase")

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

    async def _apply_wallet_reward(self, uow: UnitOfWork, promo: Promocode, user: User) -> None:
        match promo.reward_type:
            case RewardType.BALANCE:
                user.balance_minor += promo.reward_value
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
