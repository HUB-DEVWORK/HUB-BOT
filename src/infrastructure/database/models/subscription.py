"""Subscription — each one is its OWN Remnawave panel user (ADR-0003).

``short_id`` is a permanent, unique per-subscription suffix. It is generated once at
creation and never derived from the mutable ``id``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import SubscriptionStatus
from src.infrastructure.database.base import (
    AwareDateTime,
    Base,
    BigInt,
    IntPk,
    JsonB,
    TimestampMixin,
)

if TYPE_CHECKING:
    from src.infrastructure.database.models.user import User


# Enum(native_enum=False) persists the member NAME ('ACTIVE'), not .value ('active'),
# so the partial-index predicate must match NAMES. Derived from the enum to prevent drift.
_LIVE_STATUS_SQL = ", ".join(
    f"'{s.name}'"
    for s in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL, SubscriptionStatus.LIMITED)
)


class Subscription(IntPk, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        # At most one live subscription per (user, plan). Partial-unique (gotcha #2).
        Index(
            "uq_active_sub",
            "user_id",
            "plan_id",
            unique=True,
            postgresql_where=text(f"status IN ({_LIVE_STATUS_SQL})"),
            sqlite_where=text(f"status IN ({_LIVE_STATUS_SQL})"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # --- panel linkage -----------------------------------------------------
    remnawave_uuid: Mapped[uuid.UUID | None] = mapped_column(Uuid())
    short_id: Mapped[str] = mapped_column(String(16), unique=True)

    # --- plan + frozen snapshot -------------------------------------------
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False, length=16),
        default=SubscriptionStatus.PENDING,
        index=True,
    )
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_by_channel_leave: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- limits / usage ----------------------------------------------------
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInt, default=0)  # 0 -> unlimited
    traffic_used_bytes: Mapped[int] = mapped_column(BigInt, default=0)
    device_limit: Mapped[int | None] = mapped_column()
    traffic_limit_strategy: Mapped[str | None] = mapped_column(String(32))
    internal_squads: Mapped[list[Any]] = mapped_column(JsonB, default=list)
    external_squad: Mapped[str | None] = mapped_column(String(36))

    # --- lifecycle timestamps ---------------------------------------------
    start_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    expire_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime, index=True)
    subscription_url: Mapped[str | None] = mapped_column(String(512))
    crypto_link: Mapped[str | None] = mapped_column(String(512))  # happ link

    # --- autopay -----------------------------------------------------------
    autopay_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    autopay_days_before: Mapped[int] = mapped_column(default=1)
    autopay_period_days: Mapped[int | None] = mapped_column()
    # Opt-in to charge the user's saved card when the balance is short.
    autopay_card_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    autopay_card_attempted_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)

    # --- audit -------------------------------------------------------------
    device_reset_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    link_reset_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    last_webhook_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    last_revoke_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)

    user: Mapped[User] = relationship(back_populates="subscriptions")

    @property
    def is_usable(self) -> bool:
        return self.status.is_usable
