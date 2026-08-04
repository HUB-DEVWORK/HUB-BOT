"""BroadcastTemplate — a saved broadcast composer preset the owner can reuse (screen 07).

Just the text plus the inline-button/emoji config (no media — templates outlive uploaded
files). Applying one refills the composer; it is never sent on its own.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, IntPk, JsonB, TimestampMixin


class BroadcastTemplate(IntPk, TimestampMixin, Base):
    __tablename__ = "broadcast_templates"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(String(4096), default="")
    # Button + emoji config so applying a template restores the whole composer:
    # {button_enabled, button_text, button_kind, button_url, button_action, button_color, emoji_id}.
    extras: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
