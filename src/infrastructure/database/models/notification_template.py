"""NotificationTemplate — the owner-editable text for one lifecycle event (admin screen 08).

One row per stage the bot messages a user about (welcome, purchase, top-up, renewal,
autopay, referral reward, refund, plan change, traffic top-up, expiry). The owner edits
the text and toggles each on/off from the cabinet or the in-bot admin; the notification
sites render the row's ``text`` with ``{placeholders}`` instead of a hardcoded string, so
every user-facing message is data, not code. Missing events are seeded on boot.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, IntPk, TimestampMixin


class NotificationTemplate(IntPk, TimestampMixin, Base):
    __tablename__ = "notification_templates"
    __table_args__ = (UniqueConstraint("event", name="uq_notification_event"),)

    event: Mapped[str] = mapped_column(String(64))  # stable key, e.g. "purchase"
    text: Mapped[str] = mapped_column(String(4096))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
