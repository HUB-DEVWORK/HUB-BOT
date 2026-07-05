"""PaymentGateway — admin-editable runtime config for a provider (ADR-0004).

``settings`` holds provider credentials; secret fields are Fernet-encrypted at rest with
``APP__CRYPT_KEY`` before being stored (see infrastructure/payments).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import Currency, PaymentGatewayType
from src.infrastructure.database.base import Base, IntPk, JsonB, TimestampMixin


class PaymentGateway(IntPk, TimestampMixin, Base):
    __tablename__ = "payment_gateways"

    type: Mapped[PaymentGatewayType] = mapped_column(
        Enum(PaymentGatewayType, native_enum=False, length=24), unique=True
    )
    order_index: Mapped[int] = mapped_column(default=0)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, native_enum=False, length=8), default=Currency.RUB
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(String(64))
    # Per-provider credentials/options; secrets stored Fernet-encrypted.
    settings: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
