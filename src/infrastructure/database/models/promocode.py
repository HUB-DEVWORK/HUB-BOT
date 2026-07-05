"""Promocode + per-user activation ledger (docs/context/04)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import Availability, RewardType
from src.infrastructure.database.base import (
    AwareDateTime,
    Base,
    IntPk,
    JsonB,
    TimestampMixin,
    utcnow,
)


class Promocode(IntPk, TimestampMixin, Base):
    __tablename__ = "promocodes"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    reward_type: Mapped[RewardType] = mapped_column(Enum(RewardType, native_enum=False, length=24))
    reward_value: Mapped[int] = mapped_column(default=0)
    plan_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JsonB)  # for SUBSCRIPTION reward
    promo_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("promo_groups.id", ondelete="SET NULL")
    )

    availability: Mapped[Availability] = mapped_column(
        Enum(Availability, native_enum=False, length=16), default=Availability.ALL
    )
    first_purchase_only: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    max_activations: Mapped[int | None] = mapped_column()  # None -> unlimited
    is_reusable: Mapped[bool] = mapped_column(Boolean, default=False)

    activations: Mapped[list[PromocodeActivation]] = relationship(
        back_populates="promocode", cascade="all, delete-orphan"
    )


class PromocodeActivation(IntPk, Base):
    __tablename__ = "promocode_activations"
    __table_args__ = (Index("uq_promo_user", "promocode_id", "user_id", unique=True),)

    promocode_id: Mapped[int] = mapped_column(
        ForeignKey("promocodes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    activated_at: Mapped[dt.datetime] = mapped_column(AwareDateTime, default=utcnow)

    promocode: Mapped[Promocode] = relationship(back_populates="activations")
