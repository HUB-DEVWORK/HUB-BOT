"""BlacklistEntry — a permanently banned Telegram id (admin screen 12, «Безопасность»).

When ``BLACKLIST_CHECK_ENABLED`` is on, the bot middleware ignores every update from a
listed id (like a blocked user, but by id rather than account status, so it survives even
if the user re-registers). Managed from the cabinet: add/remove with an optional reason.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, IntPk, TimestampMixin


class BlacklistEntry(IntPk, TimestampMixin, Base):
    __tablename__ = "blacklist"
    __table_args__ = (UniqueConstraint("telegram_id", name="uq_blacklist_tg"),)

    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reason: Mapped[str] = mapped_column(String(256), default="")
