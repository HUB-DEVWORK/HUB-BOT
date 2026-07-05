"""Transaction — the money ledger with dual idempotency keys (docs/context/03)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from src.infrastructure.database.models.user import User

from src.core.enums import (
    Currency,
    PaymentGatewayType,
    PurchaseType,
    TransactionStatus,
    TransactionType,
)
from src.infrastructure.database.base import (
    AwareDateTime,
    Base,
    BigInt,
    IntPk,
    JsonB,
    TimestampMixin,
)


class Transaction(IntPk, TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        # Webhook idempotency: a provider payment id is unique per gateway.
        Index("uq_external_gateway", "external_id", "gateway_type", unique=True),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, native_enum=False, length=24)
    )

    # Internal idempotency key (generated up-front, unique).
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid(), unique=True, default=uuid.uuid4)
    # Provider-side id (filled once the gateway assigns one).
    external_id: Mapped[str | None] = mapped_column(String(128))

    gateway_type: Mapped[PaymentGatewayType | None] = mapped_column(
        Enum(PaymentGatewayType, native_enum=False, length=24)
    )
    gateway_display_name: Mapped[str | None] = mapped_column(String(64))
    payment_method: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, native_enum=False, length=16),
        default=TransactionStatus.PENDING,
        index=True,
    )

    amount_minor: Mapped[int] = mapped_column(BigInt)
    currency: Mapped[Currency] = mapped_column(Enum(Currency, native_enum=False, length=8))

    # Frozen snapshots (gotcha #8): what was quoted / ordered at purchase time.
    pricing: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    plan_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JsonB)
    purchase_type: Mapped[PurchaseType | None] = mapped_column(
        Enum(PurchaseType, native_enum=False, length=16)
    )

    is_test: Mapped[bool] = mapped_column(default=False)

    # Tax receipt (generated later via a post-completion hook).
    receipt_uuid: Mapped[str | None] = mapped_column(String(64))
    receipt_created_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)

    completed_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)

    user: Mapped[User] = relationship(back_populates="transactions")
