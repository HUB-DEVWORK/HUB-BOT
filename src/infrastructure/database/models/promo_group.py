"""PromoGroup — priority-based discount tiers + user membership (docs/context/04)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import (
    AwareDateTime,
    Base,
    BigInt,
    IntPk,
    JsonB,
    utcnow,
)


class PromoGroup(IntPk, Base):
    __tablename__ = "promo_groups"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    priority: Mapped[int] = mapped_column(default=0, index=True)  # higher wins
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    server_discount_pct: Mapped[int] = mapped_column(default=0)
    traffic_discount_pct: Mapped[int] = mapped_column(default=0)
    device_discount_pct: Mapped[int] = mapped_column(default=0)
    period_discounts: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)  # {days: pct}

    # Auto-assign this group once a user's lifetime spend reaches the threshold.
    auto_assign_total_spent_minor: Mapped[int | None] = mapped_column(BigInt)
    apply_discounts_to_addons: Mapped[bool] = mapped_column(Boolean, default=False)


class UserPromoGroup(Base):
    """Membership association (M2M). Effective group = highest priority."""

    __tablename__ = "user_promo_groups"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    promo_group_id: Mapped[int] = mapped_column(
        ForeignKey("promo_groups.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[dt.datetime] = mapped_column(AwareDateTime, default=utcnow)
    assigned_by: Mapped[int | None] = mapped_column()  # actor id, or None for auto-assign
