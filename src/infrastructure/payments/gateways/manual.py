"""Manual / admin-confirmed gateway. Always available; no external provider.

Flow: ``create_payment`` returns PENDING (the bot tells the user an admin will confirm).
An admin action posts to the webhook route with the payment id to complete it. Optionally
guarded by a shared ``secret`` in the gateway settings.
"""

from __future__ import annotations

import hmac
import uuid

from src.application.common.payments import (
    GatewayCapabilities,
    PaymentContext,
    PaymentResult,
    PaymentResultKind,
    WebhookRequest,
    WebhookResult,
)
from src.core.enums import Currency, PaymentGatewayType, TransactionStatus
from src.core.exceptions import WebhookVerificationError
from src.infrastructure.payments.base import BasePaymentGateway

_STATUS_MAP = {
    "completed": TransactionStatus.COMPLETED,
    "confirm": TransactionStatus.COMPLETED,
    "canceled": TransactionStatus.CANCELED,
    "reject": TransactionStatus.CANCELED,
}


class ManualGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.MANUAL

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset(Currency), needs_http_webhook=True)

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        return PaymentResult(kind=PaymentResultKind.PENDING, external_id=str(ctx.payment_id))

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        secret = str(self.settings.get("secret") or "")
        if secret and not hmac.compare_digest(request.headers.get("x-admin-secret") or "", secret):
            raise WebhookVerificationError("manual gateway: bad admin secret")
        data = self.parse_json(request.body)
        raw_id = str(data.get("payment_id") or "")
        try:
            payment_id = uuid.UUID(raw_id)
        except ValueError as exc:
            raise WebhookVerificationError("manual gateway: missing/invalid payment_id") from exc
        status = _STATUS_MAP.get(str(data.get("status") or "completed").lower())
        if status is None:
            raise WebhookVerificationError("manual gateway: unknown status")
        return WebhookResult(status=status, payment_id=payment_id)
