"""Partner — a reseller/affiliate with their own link, markup and revenue share.

The owner onboards partners (screen «Партнёры»): each gets a ``code`` for a deep link
(?start=partner_<code>), an optional ``markup_pct`` they add on top of the base price and
a ``revenue_share_pct`` cut of the turnover they drive. ``turnover_minor`` / ``earnings_minor``
accrue as their referred users pay (wired at payment fulfilment).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, IntPk, TimestampMixin


class Partner(IntPk, TimestampMixin, Base):
    __tablename__ = "partners"
    __table_args__ = (UniqueConstraint("code", name="uq_partner_code"),)

    name: Mapped[str] = mapped_column(String(128))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    code: Mapped[str] = mapped_column(String(32))  # deep-link suffix: ?start=partner_<code>
    markup_pct: Mapped[int] = mapped_column(Integer, default=0)  # added on top of the base price
    revenue_share_pct: Mapped[int] = mapped_column(Integer, default=0)  # partner's cut of turnover
    turnover_minor: Mapped[int] = mapped_column(BigInteger, default=0)  # total driven
    earnings_minor: Mapped[int] = mapped_column(BigInteger, default=0)  # accrued share
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
