"""ReferralService — binding + commission-on-topup with at-most-once payout (gotcha #13)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from src.application.common.events import EventBus
from src.application.events import ReferralRewardIssued
from src.core.enums import Currency, ReferralLevel, TransactionStatus, TransactionType
from src.infrastructure.database.models.referral import Referral, ReferralEarning
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork

DEFAULT_COMMISSION_PERCENT = 25


class ReferralService:
    def __init__(self, event_bus: EventBus) -> None:
        self._events = event_bus

    async def bind(self, uow: UnitOfWork, referred: User, referrer_code: str) -> Referral | None:
        """Bind ``referred`` to the owner of ``referrer_code`` (one referrer per user)."""
        referrer = await uow.users.get_by_referral_code(referrer_code)
        if referrer is None or referrer.id == referred.id:
            return None
        if await uow.referrals.get_by_referred(referred.id) is not None:
            return None
        referral = Referral(
            referrer_id=referrer.id, referred_id=referred.id, level=ReferralLevel.FIRST
        )
        await uow.referrals.add(referral)
        referred.referred_by_id = referrer.id
        return referral

    @staticmethod
    def commission(amount_minor: int, percent: int) -> int:
        return int((Decimal(amount_minor) * Decimal(percent) / Decimal(100)).to_integral_value())

    async def reward_on_topup(
        self, uow: UnitOfWork, *, payer: User, amount_minor: int, transaction_id: int
    ) -> ReferralEarning | None:
        """Pay the referrer a commission for ``payer``'s top-up. Idempotent per transaction."""
        referral = await uow.referrals.get_by_referred(payer.id)
        if referral is None:
            return None
        referrer = await uow.users.get(referral.referrer_id)
        if referrer is None:
            return None

        # At-most-once: one earning per (referrer, source transaction).
        existing = await uow.referral_earnings.find_one(
            user_id=referrer.id, transaction_id=transaction_id
        )
        if existing is not None:
            return existing

        percent = referrer.referral_commission_percent or DEFAULT_COMMISSION_PERCENT
        reward = self.commission(amount_minor, percent)
        if reward <= 0:
            return None

        earning = ReferralEarning(
            user_id=referrer.id,
            referral_id=referral.id,
            amount_minor=reward,
            reason="topup_commission",
            transaction_id=transaction_id,
            is_issued=True,
        )
        await uow.referral_earnings.add(earning)
        await uow.users.increment_balance(referrer, reward)  # atomic (no lost update)
        await uow.transactions.add(
            Transaction(
                user_id=referrer.id,
                type=TransactionType.REFERRAL_REWARD,
                status=TransactionStatus.COMPLETED,
                amount_minor=reward,
                currency=referrer.currency or Currency.RUB,
            )
        )
        await self._events.publish(
            ReferralRewardIssued(referrer_id=referrer.id, referred_id=payer.id, amount_minor=reward)
        )
        return earning
