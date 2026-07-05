"""Plan catalogue — normalized plan / duration / per-currency price (from remnashop)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import Availability, Currency, PlanType
from src.infrastructure.database.base import Base, BigInt, IntPk, JsonB, TimestampMixin

if TYPE_CHECKING:
    pass


class Plan(IntPk, TimestampMixin, Base):
    __tablename__ = "plans"

    public_code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(String(1024))
    type: Mapped[PlanType] = mapped_column(
        Enum(PlanType, native_enum=False, length=16), default=PlanType.BOTH
    )
    availability: Mapped[Availability] = mapped_column(
        Enum(Availability, native_enum=False, length=16), default=Availability.ALL
    )

    traffic_limit_bytes: Mapped[int | None] = mapped_column(BigInt)  # None/0 -> unlimited
    device_limit: Mapped[int | None] = mapped_column()
    traffic_limit_strategy: Mapped[str | None] = mapped_column(String(32))

    # Access allow-lists (used when availability == ALLOWED).
    allowed_telegram_ids: Mapped[list[Any]] = mapped_column(JsonB, default=list)
    allowed_emails: Mapped[list[Any]] = mapped_column(JsonB, default=list)

    # Remnawave squad UUIDs (stored as strings for portability).
    internal_squads: Mapped[list[Any]] = mapped_column(JsonB, default=list)
    external_squad: Mapped[str | None] = mapped_column(String(36))

    order_index: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)

    durations: Mapped[list[PlanDuration]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="PlanDuration.order_index"
    )


class PlanDuration(IntPk, Base):
    __tablename__ = "plan_durations"

    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    days: Mapped[int] = mapped_column()
    order_index: Mapped[int] = mapped_column(default=0)

    plan: Mapped[Plan] = relationship(back_populates="durations")
    prices: Mapped[list[PlanPrice]] = relationship(
        back_populates="duration", cascade="all, delete-orphan"
    )


class PlanPrice(IntPk, Base):
    __tablename__ = "plan_prices"

    plan_duration_id: Mapped[int] = mapped_column(
        ForeignKey("plan_durations.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[Currency] = mapped_column(Enum(Currency, native_enum=False, length=8))
    price_minor: Mapped[int] = mapped_column(BigInt)

    duration: Mapped[PlanDuration] = relationship(back_populates="prices")
