"""ReminderStep — one rung of the subscription-expiry reminder ladder (admin screen 08).

Each step fires ``hours_before`` hours before a subscription's ``expire_at`` (0 = at the
moment of expiry). ``text`` supports the ``{hours}`` / ``{time}`` / ``{plan}`` placeholders.
The scheduler messages every active/trial subscriber whose expiry enters the step's window,
once per subscription per step. Hour precision replaces the day-only ``SmartReminder``, so
the owner can warn at 24 h / 12 h / 1 h — and add/remove/edit any step from the cabinet.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, IntPk, TimestampMixin


class ReminderStep(IntPk, TimestampMixin, Base):
    __tablename__ = "reminder_steps"
    __table_args__ = (UniqueConstraint("hours_before", name="uq_reminder_hours"),)

    hours_before: Mapped[int] = mapped_column(Integer)  # hours before expire_at; 0 = at expiry
    text: Mapped[str] = mapped_column(
        String(4096),
        default="Подписка истекает через {time}. Продлите, чтобы не потерять доступ.",
    )
    button_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # «Продлить» → mini-app
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
