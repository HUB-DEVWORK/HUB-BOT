"""MiniappConfig — singleton row: which mini-app template is live and its branding.

Admin screen 06. ``template`` maps to ``miniapp/templates.json`` ids; the cabinet API
exposes this to the mini-app at load so the chosen theme renders for end users.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import AwareDateTime, Base, IntPk, JsonB, TimestampMixin


class MiniappConfig(IntPk, TimestampMixin, Base):
    __tablename__ = "miniapp_config"

    template: Mapped[str] = mapped_column(String(32), default="minimal")
    title: Mapped[str | None] = mapped_column(String(64))
    greeting: Mapped[str | None] = mapped_column(String(256))
    accent_color: Mapped[str | None] = mapped_column(String(9))  # #RRGGBB
    photo_scale_pct: Mapped[int] = mapped_column(default=100)  # 70..130
    cover_path: Mapped[str | None] = mapped_column(String(512))
    # Free-form UI overrides: {"buttons": {key: {text, color}}, "sections": [...],
    # "scale": 100} — consumed by the mini-app at load.
    ui: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    published_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
