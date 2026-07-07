"""Web-cabinet refresh tokens — one row per issued refresh JWT.

Only the SHA-256 hash of the token is stored, so a DB leak can't replay sessions.
Refresh is ROTATED on use (the old row is revoked, a new one issued) — a stolen
refresh token is invalidated the moment the legitimate user refreshes.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import AwareDateTime, Base, IntPk, TimestampMixin


class CabinetRefreshToken(IntPk, TimestampMixin, Base):
    __tablename__ = "cabinet_refresh_tokens"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_info: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[dt.datetime] = mapped_column(AwareDateTime, index=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
