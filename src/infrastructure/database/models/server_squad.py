"""ServerSquad — local mirror of a Remnawave internal squad (server/location).

Synced from the panel at startup; carries local pricing, capacity and promo-group gating.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, BigInt, IntPk, TimestampMixin


class ServerSquad(IntPk, TimestampMixin, Base):
    __tablename__ = "server_squads"

    squad_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    original_name: Mapped[str | None] = mapped_column(String(128))
    country_code: Mapped[str | None] = mapped_column(String(2))

    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_trial_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    price_minor: Mapped[int] = mapped_column(BigInt, default=0)

    sort_order: Mapped[int] = mapped_column(default=0)
    max_users: Mapped[int | None] = mapped_column()  # None -> uncapped
    current_users: Mapped[int] = mapped_column(default=0)

    @property
    def has_capacity(self) -> bool:
        return self.max_users is None or self.current_users < self.max_users
