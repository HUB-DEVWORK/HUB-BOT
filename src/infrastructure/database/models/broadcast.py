"""Broadcast — one mass mailing job (admin screen 07).

Created by the cabinet, delivered by a taskiq worker that updates ``sent``/``failed``
as it goes; the UI polls for live progress. ``audience`` is resolved to a user set at
send time (not frozen), matching the composer's segment counters.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import BroadcastAudience, BroadcastMedia, BroadcastStatus
from src.infrastructure.database.base import AwareDateTime, Base, IntPk, TimestampMixin


class Broadcast(IntPk, TimestampMixin, Base):
    __tablename__ = "broadcasts"

    audience: Mapped[BroadcastAudience] = mapped_column(
        Enum(BroadcastAudience, native_enum=False, length=16), default=BroadcastAudience.ALL
    )
    media: Mapped[BroadcastMedia] = mapped_column(
        Enum(BroadcastMedia, native_enum=False, length=16), default=BroadcastMedia.TEXT
    )
    text: Mapped[str] = mapped_column(String(4096))
    media_path: Mapped[str | None] = mapped_column(String(512))
    # Optional inline button (e.g. a "Renew with discount" link opening the mini-app).
    button_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    button_text: Mapped[str | None] = mapped_column(String(64))
    button_url: Mapped[str | None] = mapped_column(String(512))
    # Alternative to url: a built-in bot action (act:<code>); plus Telegram button
    # style derived from a HEX color, like menu-constructor buttons.
    button_action: Mapped[str | None] = mapped_column(String(64))
    button_color: Mapped[str | None] = mapped_column(String(9))
    # Premium custom emoji id, prepended to the message text as <tg-emoji>.
    emoji_id: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[BroadcastStatus] = mapped_column(
        Enum(BroadcastStatus, native_enum=False, length=16),
        default=BroadcastStatus.PENDING,
        index=True,
    )
    total: Mapped[int] = mapped_column(default=0)
    sent: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    finished_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
