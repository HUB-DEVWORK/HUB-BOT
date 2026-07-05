"""Framework-agnostic enums shared across all layers.

These are the vocabulary of the domain. Keep them free of infrastructure imports.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Role(IntEnum):
    """User roles, ordered by privilege. Higher value == more privilege.

    ``SYSTEM`` is the synthetic actor (id -1) used by webhooks / workers / seeding to
    bypass RBAC. Use :meth:`includes` for hierarchy checks rather than ``==``.
    """

    USER = 0
    PREVIEW = 1
    ADMIN = 2
    DEV = 3
    OWNER = 4
    SYSTEM = 5

    def includes(self, other: Role) -> bool:
        """True if this role is at least as privileged as ``other``."""
        return self.value >= other.value

    @property
    def is_staff(self) -> bool:
        return self.value >= Role.ADMIN.value


class Permission(StrEnum):
    """Fine-grained permissions. Mapped from roles in the RBAC layer."""

    VIEW_DASHBOARD = "view_dashboard"
    MANAGE_USERS = "manage_users"
    MANAGE_PLANS = "manage_plans"
    MANAGE_PROMOCODES = "manage_promocodes"
    MANAGE_GATEWAYS = "manage_gateways"
    MANAGE_SETTINGS = "manage_settings"
    ISSUE_REFUND = "issue_refund"
    BROADCAST = "broadcast"
    VIEW_STATISTICS = "view_statistics"


class Locale(StrEnum):
    """Supported UI languages."""

    EN = "en"
    RU = "ru"

    @classmethod
    def default(cls) -> Locale:
        return cls.RU


class Currency(StrEnum):
    """Currencies. ``XTR`` = Telegram Stars (integer, exponent 0)."""

    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    USDT = "USDT"
    XTR = "XTR"

    @property
    def exponent(self) -> int:
        """Number of minor-unit decimal places (kopeks/cents = 2, Stars = 0)."""
        return _CURRENCY_EXPONENT[self]


_CURRENCY_EXPONENT: dict[Currency, int] = {
    Currency.RUB: 2,
    Currency.USD: 2,
    Currency.EUR: 2,
    Currency.USDT: 2,
    Currency.XTR: 0,
}


class SubscriptionStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    LIMITED = "limited"  # over traffic limit but not expired
    EXPIRED = "expired"
    DISABLED = "disabled"
    PENDING = "pending"  # awaiting first payment
    DELETED = "deleted"

    @property
    def is_usable(self) -> bool:
        """Status under which the subscription counts as a live 'active' one."""
        return self in {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIAL,
            SubscriptionStatus.LIMITED,
        }


class PlanType(StrEnum):
    TRAFFIC = "traffic"
    DEVICES = "devices"
    BOTH = "both"
    UNLIMITED = "unlimited"


class Availability(StrEnum):
    """Who a plan / promocode is available to."""

    ALL = "all"
    NEW = "new"
    EXISTING = "existing"
    INVITED = "invited"
    ALLOWED = "allowed"  # explicit allow-list (telegram ids / emails)
    LINK = "link"  # only via a direct link/code


class PurchaseType(StrEnum):
    NEW = "new"
    RENEW = "renew"
    CHANGE = "change"


class TransactionType(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    SUBSCRIPTION_PAYMENT = "subscription_payment"
    REFUND = "refund"
    REFERRAL_REWARD = "referral_reward"
    GIFT = "gift"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    FAILED = "failed"


# Allowed CAS transitions for the idempotent payment state machine.
# key -> set of statuses it may be reached FROM.
TRANSACTION_TRANSITIONS: dict[TransactionStatus, frozenset[TransactionStatus]] = {
    TransactionStatus.COMPLETED: frozenset({TransactionStatus.PENDING}),
    TransactionStatus.CANCELED: frozenset({TransactionStatus.PENDING}),
    TransactionStatus.FAILED: frozenset({TransactionStatus.PENDING}),
    TransactionStatus.REFUNDED: frozenset({TransactionStatus.COMPLETED}),
}


class PaymentGatewayType(StrEnum):
    """Payment providers. Only ``MANUAL`` and ``TELEGRAM_STARS`` ship active in the base;
    the rest are added later as single-file drop-ins (see docs/context/03-payments.md)."""

    MANUAL = "manual"  # admin-confirmed payment, always available
    TELEGRAM_STARS = "telegram_stars"
    YOOKASSA = "yookassa"
    CRYPTOMUS = "cryptomus"
    CRYPTOBOT = "cryptobot"
    TRIBUTE = "tribute"


class RewardType(StrEnum):
    """Promocode reward kinds."""

    BALANCE = "balance"
    DURATION = "duration"
    TRAFFIC = "traffic"
    DEVICES = "devices"
    SUBSCRIPTION = "subscription"
    PROMO_GROUP = "promo_group"
    PERSONAL_DISCOUNT = "personal_discount"
    PURCHASE_DISCOUNT = "purchase_discount"


class ReferralLevel(IntEnum):
    FIRST = 1
    SECOND = 2


class UserStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"


class AuthType(StrEnum):
    TELEGRAM = "telegram"
    EMAIL = "email"
    OAUTH = "oauth"
