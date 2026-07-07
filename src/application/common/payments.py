"""PaymentGateway protocol + request/result DTOs (ADR-0004, docs/context/03).

Every provider implements one ABC. A single webhook route dispatches to the right gateway.
Add a provider = one file + one enum value + one DB seed row; the route never changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.core.enums import Currency, PaymentGatewayType, TransactionStatus
from src.core.money import Money


class PaymentResultKind(StrEnum):
    REDIRECT = "redirect"  # hosted invoice URL to send the user to
    IN_BOT = "in_bot"  # payload for a Telegram in-bot invoice (e.g. Stars)
    PENDING = "pending"  # awaiting out-of-band confirmation (e.g. manual/admin)


@dataclass(frozen=True, slots=True)
class PaymentContext:
    """Everything a gateway needs to open a payment for one transaction."""

    payment_id: UUID
    amount: Money
    description: str
    user_id: int
    telegram_id: int | None = None
    return_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PaymentResult:
    kind: PaymentResultKind
    external_id: str | None = None
    redirect_url: str | None = None
    invoice_payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    """A raw inbound provider callback, before verification."""

    body: bytes
    headers: dict[str, str]
    query: dict[str, str] = field(default_factory=dict)
    client_ip: str | None = None


@dataclass(frozen=True, slots=True)
class SavedPaymentMethod:
    """A charge token the provider saved for merchant-initiated payments (recurring)."""

    method_id: str
    title: str | None = None  # human label, e.g. "Bank card *4444"


@dataclass(frozen=True, slots=True)
class WebhookResult:
    """The verified outcome: which payment, and its new status."""

    status: TransactionStatus
    payment_id: UUID | None = None
    external_id: str | None = None
    amount: Money | None = None  # for optional amount cross-check
    saved_method: SavedPaymentMethod | None = None  # card the provider saved for autopay


@dataclass(frozen=True, slots=True)
class GatewayCapabilities:
    currencies: frozenset[Currency]
    needs_http_webhook: bool = True
    supports_refund: bool = False
    supports_recurrent: bool = False
    supports_saved_method: bool = False


@runtime_checkable
class PaymentGateway(Protocol):
    """One payment provider behind a uniform interface."""

    gateway_type: PaymentGatewayType

    @property
    def capabilities(self) -> GatewayCapabilities: ...

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult: ...

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        """Verify + parse a provider callback.

        Raises ``WebhookVerificationError`` on signature/IP failure (-> HTTP 403) and
        ``NotFound`` on an unknown payment (-> HTTP 404).
        """
        ...
