"""ReferralService — binding + commission-on-topup with at-most-once payout (gotcha #13)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from src.application.common.events import EventBus
from src.application.events import ReferralRewardIssued
from src.core.enums import Currency, ReferralLevel, TransactionStatus, TransactionType
from src.infrastructure.database.models.referral import Referral, ReferralEarning
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from src.application.services.bot_config import BotConfigService
    from src.application.services.subscription import SubscriptionService
    from src.infrastructure.database.uow import UnitOfWork

DEFAULT_COMMISSION_PERCENT = 25


class ReferralService:
    def __init__(
        self,
        event_bus: EventBus,
        subscriptions: SubscriptionService | None = None,
        config: BotConfigService | None = None,
    ) -> None:
        self._events = event_bus
        self._subscriptions = subscriptions
        self._config = config

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

        # Per-user override wins; otherwise the shop-wide REFERRAL_PERCENT the owner set (and
        # that we advertise to users) — never a silent hardcoded rate (REF-1).
        default_pct = DEFAULT_COMMISSION_PERCENT
        if self._config is not None:
            default_pct = int(await self._config.value(uow, "REFERRAL_PERCENT") or default_pct)
        # Clamp to [0,100] — a fat-fingered REFERRAL_PERCENT (150 instead of 15) would otherwise
        # credit the referrer MORE than the user actually paid, bleeding real money from the shop.
        percent = min(100, max(0, referrer.referral_commission_percent or default_pct))
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
        await self._grant_signup_days(uow, referral_id=referral.id, referrer=referrer, payer=payer)
        return earning

    async def _grant_signup_days(
        self, uow: UnitOfWork, *, referral_id: int, referrer: User, payer: User
    ) -> None:
        """One-time «+N дней тебе и другу» when the referral first brings money in.

        Soft-fails per party: a side without a usable subscription simply gets nothing
        (consistent with the commission's soft-fail rule). Idempotent via a zero-amount
        ledger row, so a retried webhook can't double-extend.
        """
        if self._subscriptions is None or self._config is None:
            return
        days = int(await self._config.value(uow, "REFERRAL_BONUS_DAYS") or 0)
        if days <= 0:
            return
        # Claim the one-time slot in the DB BEFORE renewing (renew extends on the panel — a side
        # effect we cannot roll back). Insert the marker in a SAVEPOINT + flush: the partial-unique
        # index serializes concurrent workers (a conflicting insert blocks then fails), so only the
        # winner renews. The loser's savepoint rolls back without poisoning the outer transaction
        # or double-extending. Replaces the old check-then-insert that two workers could both pass.
        await uow.session.flush()  # persist the commission etc. before the savepoint claim
        marker = ReferralEarning(
            user_id=referrer.id,
            referral_id=referral_id,
            amount_minor=0,
            reason="signup_days_bonus",
            is_issued=True,
        )
        try:
            async with uow.session.begin_nested():
                uow.session.add(marker)
                await uow.session.flush()
        except IntegrityError:
            return  # another worker already claimed this referral's signup bonus (at-most-once)
        for party in (referrer, payer):
            sub = (
                await uow.subscriptions.get(party.current_subscription_id)
                if party.current_subscription_id
                else None
            )
            if sub is not None and sub.status.is_usable:
                await self._subscriptions.renew(uow, sub, days=days, telegram_id=party.telegram_id)
