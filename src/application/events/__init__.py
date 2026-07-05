"""Concrete domain events published by services."""

from __future__ import annotations

from dataclasses import dataclass

from src.application.common.events import DomainEvent
from src.core.enums import PurchaseType


@dataclass(frozen=True, slots=True)
class UserRegistered(DomainEvent):
    user_id: int = 0
    telegram_id: int | None = None
    referred_by_id: int | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionPurchased(DomainEvent):
    user_id: int = 0
    subscription_id: int = 0
    transaction_id: int = 0
    purchase_type: PurchaseType = PurchaseType.NEW


@dataclass(frozen=True, slots=True)
class PaymentCompleted(DomainEvent):
    user_id: int = 0
    transaction_id: int = 0
    amount_minor: int = 0
    currency: str = ""


@dataclass(frozen=True, slots=True)
class TrialGranted(DomainEvent):
    user_id: int = 0
    subscription_id: int = 0


@dataclass(frozen=True, slots=True)
class ReferralRewardIssued(DomainEvent):
    referrer_id: int = 0
    referred_id: int = 0
    amount_minor: int = 0


@dataclass(frozen=True, slots=True)
class SubscriptionExpired(DomainEvent):
    user_id: int = 0
    subscription_id: int = 0
