"""User model — the account, wallet, referral identity and discount state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import AuthType, Currency, Locale, Role, UserStatus
from src.infrastructure.database.base import Base, BigInt, IntPk, JsonB, TimestampMixin

if TYPE_CHECKING:
    from src.infrastructure.database.models.subscription import Subscription
    from src.infrastructure.database.models.transaction import Transaction


class User(IntPk, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_referral_code", "referral_code", unique=True),)

    telegram_id: Mapped[int | None] = mapped_column(BigInt, unique=True, index=True)
    auth_type: Mapped[AuthType] = mapped_column(
        Enum(AuthType, native_enum=False, length=16), default=AuthType.TELEGRAM
    )
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[Locale] = mapped_column(
        Enum(Locale, native_enum=False, length=8), default=Locale.default()
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False, length=16), default=UserStatus.ACTIVE
    )
    role: Mapped[Role] = mapped_column(default=Role.USER)

    # --- wallet (minor units) ---------------------------------------------
    balance_minor: Mapped[int] = mapped_column(BigInt, default=0)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, native_enum=False, length=8), default=Currency.RUB
    )

    # --- referral identity -------------------------------------------------
    referral_code: Mapped[str] = mapped_column(String(16))
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    referral_commission_percent: Mapped[int | None] = mapped_column()

    # --- acquisition attribution (ad campaign deep-link, admin screen 09) ---
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), index=True
    )

    # --- discounts ---------------------------------------------------------
    personal_discount_pct: Mapped[int] = mapped_column(default=0)  # persists
    purchase_discount_pct: Mapped[int] = mapped_column(default=0)  # one-shot

    # --- lifecycle flags ---------------------------------------------------
    is_trial_available: Mapped[bool] = mapped_column(Boolean, default=True)
    has_had_paid_subscription: Mapped[bool] = mapped_column(Boolean, default=False)
    has_made_first_topup: Mapped[bool] = mapped_column(Boolean, default=False)
    is_rules_accepted: Mapped[bool] = mapped_column(Boolean, default=False)

    # App-level pointer to the "current" subscription (no DB FK to avoid a users<->subs
    # circular constraint; integrity is maintained by SubscriptionService).
    current_subscription_id: Mapped[int | None] = mapped_column()

    # --- saved card (provider charge token for autopay, Fernet-encrypted at rest) ---
    saved_payment_method_id: Mapped[str | None] = mapped_column(String(512))
    saved_payment_method_title: Mapped[str | None] = mapped_column(String(64))

    # --- web cabinet (used later by the mini-app) --------------------------
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    notification_settings: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)

    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    transactions: Mapped[list[Transaction]] = relationship(back_populates="user")

    @property
    def is_system(self) -> bool:
        return self.role is Role.SYSTEM
