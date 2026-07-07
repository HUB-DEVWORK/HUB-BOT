"""RollyPay (rollypay.io) — payments with timestamped HMAC webhooks.

Create: POST /api/v1/payments with the ``X-API-Key`` header; ``order_id`` = our
payment_id, amount as "199.00", response carries ``payment_id`` + ``pay_url``.
Webhook: ``X-Signature = HMAC-SHA256(signing_secret, f"{timestamp}.{raw_body}")``
with the timestamp in ``X-Timestamp`` — the timestamp binding stops replay attacks.
Status ``paid`` completes; expired/canceled/chargeback close.

Settings row keys: ``api_key``, ``signing_secret`` (Fernet-encrypted at rest).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from src.application.common.payments import (
    GatewayCapabilities,
    PaymentContext,
    PaymentResult,
    PaymentResultKind,
    WebhookRequest,
    WebhookResult,
)
from src.core.enums import Currency, PaymentGatewayType, TransactionStatus
from src.core.exceptions import PaymentError, WebhookVerificationError
from src.core.logging import get_logger
from src.core.money import Money
from src.infrastructure.payments.base import BasePaymentGateway

log = get_logger(__name__)

API = "https://rollypay.io/api/v1"

_PAID = {"paid"}
_CLOSED = {"expired", "canceled", "cancelled", "chargeback"}


class RollypayGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.ROLLYPAY

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _creds(self) -> tuple[str, str]:
        api_key = str(self.settings.get("api_key") or "")
        secret = str(self.settings.get("signing_secret") or "")
        if not api_key or not secret:
            raise PaymentError("RollyPay: api_key/signing_secret not configured")
        return api_key, secret

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        api_key, _ = self._creds()
        payload: dict[str, Any] = {
            "amount": str((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "payment_currency": ctx.amount.currency.value,
            "order_id": str(ctx.payment_id),
            "description": (ctx.description or "VPN subscription")[:128],
        }
        if ctx.return_url:
            payload["success_redirect_url"] = ctx.return_url
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(f"{API}/payments", json=payload, headers={"X-API-Key": api_key})
        data = res.json() if res.status_code in (200, 201) else {}
        url = str(data.get("pay_url") or data.get("payment_url") or "")
        if not url:
            log.error("rollypay create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"RollyPay error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(data.get("payment_id") or data.get("id") or ""),
            redirect_url=url,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        _, secret = self._creds()
        headers = {k.lower(): v for k, v in request.headers.items()}
        signature = str(headers.get("x-signature") or "")
        timestamp = str(headers.get("x-timestamp") or "")
        if not signature or not timestamp:
            raise WebhookVerificationError("RollyPay: missing X-Signature/X-Timestamp")
        message = f"{timestamp}.".encode() + request.body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.lower(), signature.lower()):
            raise WebhookVerificationError("RollyPay: signature mismatch")

        body = self.parse_json(request.body)
        status_raw = str(body.get("status") or "").lower()
        if status_raw in _PAID:
            status = TransactionStatus.COMPLETED
        elif status_raw in _CLOSED:
            status = TransactionStatus.CANCELED
        else:
            status = TransactionStatus.PENDING
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(str(body.get("order_id") or ""))
        amount = None
        with contextlib.suppress(ArithmeticError, TypeError):
            amount = Money(int(Decimal(str(body.get("amount") or "0")) * 100), Currency.RUB)
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(body.get("payment_id") or body.get("id") or "") or None,
            amount=amount,
        )

    async def fetch_status(self, external_id: str) -> WebhookResult | None:
        api_key, _ = self._creds()
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                res = await http.get(
                    f"{API}/payments/{external_id}", headers={"X-API-Key": api_key}
                )
        except httpx.HTTPError:
            return None
        if res.status_code != 200:
            return None
        body = res.json()
        status_raw = str(body.get("status") or "").lower()
        if status_raw in _PAID:
            status = TransactionStatus.COMPLETED
        elif status_raw in _CLOSED:
            status = TransactionStatus.CANCELED
        else:
            return None
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(str(body.get("order_id") or ""))
        return WebhookResult(status=status, payment_id=payment_id, external_id=external_id)
