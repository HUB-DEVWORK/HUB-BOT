"""TrafficSnapshot — one daily reading of a subscription's cumulative traffic use.

A daily job records ``used_bytes`` per active subscription (one row per sub per day). The
mini-app reads the series from ``GET /api/cabinet/traffic`` and draws the usage graph,
computing per-day deltas (and treating a drop as a monthly traffic reset).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, IntPk, TimestampMixin


class TrafficSnapshot(IntPk, TimestampMixin, Base):
    __tablename__ = "traffic_snapshots"
    __table_args__ = (UniqueConstraint("subscription_id", "day", name="uq_traffic_sub_day"),)

    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[str] = mapped_column(String(10))  # "YYYY-MM-DD" (UTC)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
