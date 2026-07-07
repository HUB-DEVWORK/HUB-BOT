"""Referral-earnings withdrawal requests (manual payout, SoloBot-style partner flow).

The amount is debited from the wallet at request time (guarded debit) so it cannot be
spent twice while the request is pending; a rejection refunds it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import WithdrawalStatus
from src.infrastructure.database.base import AwareDateTime, Base, BigInt, IntPk, TimestampMixin


class WithdrawalRequest(IntPk, TimestampMixin, Base):
    __tablename__ = "withdrawal_requests"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInt)
    method: Mapped[str] = mapped_column(String(16))  # card | usdt | ton
    details: Mapped[str] = mapped_column(String(256))  # card number / wallet address
    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus, native_enum=False, length=16),
        default=WithdrawalStatus.PENDING,
        index=True,
    )
    admin_comment: Mapped[str | None] = mapped_column(String(256))
    processed_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
