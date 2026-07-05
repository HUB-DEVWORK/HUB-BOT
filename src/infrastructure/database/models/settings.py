"""Settings — a single admin-editable row of JSONB sections.

Runtime-editable configuration overlay (registration/payment toggles, requirements,
notification routing, referral strategy, backup, blacklist, menu). Seeded committed at
startup BEFORE serving requests (gotcha #16). Partial writes touch only changed sub-keys.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Enum
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import Currency
from src.infrastructure.database.base import Base, IntPk, JsonB


class Settings(IntPk, Base):
    __tablename__ = "settings"

    default_currency: Mapped[Currency] = mapped_column(
        Enum(Currency, native_enum=False, length=8), default=Currency.RUB
    )
    access: Mapped[dict[str, Any]] = mapped_column(
        JsonB, default=dict
    )  # mode/registration/payments
    requirements: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)  # rules/channel
    notifications: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)  # toggles/routes
    referral: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)  # enable/level/strategy
    backup: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    blacklist: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    menu: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
