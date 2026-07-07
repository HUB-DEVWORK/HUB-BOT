"""SaleCampaign — a recurring, limited-quantity discount the owner runs (admin screen 07).

A sale is active on days ``start_day``..``end_day`` of each month (e.g. 1..3 = the first
three days) and grants ``discount_pct`` off, capped to ``max_uses`` purchases per month
(0 = unlimited). PricingService adds the best active sale into the discount stack; the
quota is consumed at fulfilment. ``used_count`` resets when ``used_period`` (the "YYYY-MM"
it belongs to) rolls over, so "first N buyers each month" works without a cron.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, IntPk, TimestampMixin


class SaleCampaign(IntPk, TimestampMixin, Base):
    __tablename__ = "sale_campaigns"

    title: Mapped[str] = mapped_column(String(128), default="Скидка месяца")
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)  # 0..100
    start_day: Mapped[int] = mapped_column(Integer, default=1)  # day-of-month window, inclusive
    end_day: Mapped[int] = mapped_column(Integer, default=3)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)  # per month; 0 = unlimited
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    used_period: Mapped[str] = mapped_column(String(7), default="")  # "YYYY-MM" of used_count
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
