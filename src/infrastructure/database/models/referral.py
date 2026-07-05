"""Referral binding + earnings ledger (docs/context/04).

``referred_id`` is UNIQUE — one referrer per user. Earnings carry ``is_issued`` so a
retried webhook cannot double-pay (at-most-once, gotcha #13).
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import ReferralLevel
from src.infrastructure.database.base import Base, BigInt, IntPk, TimestampMixin


class Referral(IntPk, TimestampMixin, Base):
    __tablename__ = "referrals"

    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # One referrer per user.
    referred_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    level: Mapped[ReferralLevel] = mapped_column(default=ReferralLevel.FIRST)


class ReferralEarning(IntPk, TimestampMixin, Base):
    __tablename__ = "referral_earnings"

    user_id: Mapped[int] = mapped_column(  # the earner (referrer)
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    referral_id: Mapped[int] = mapped_column(ForeignKey("referrals.id", ondelete="CASCADE"))
    amount_minor: Mapped[int] = mapped_column(BigInt)
    reason: Mapped[str | None] = mapped_column(String(64))
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL")
    )
    is_issued: Mapped[bool] = mapped_column(Boolean, default=False)
