"""RioPay (api.riopay.online v2) — card/SBP orders, HMAC-SHA512 webhook.

Create: POST /v1/orders with the ``X-Api-Token`` header; ``externalId`` = our
payment_id, amount as a ruble string. Success is HTTP 201 with ``paymentLink``.
Webhook: ``X-Signature`` = HMAC-SHA512 hex over the raw body with ``webhook_secret``
(defaults to the API token). Statuses: COMPLETED paid, CANCELED/FAILED/EXPIRED closed.

Settings row keys: ``api_token`` (Fernet-encrypted at rest), optional ``webhook_secret``.
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

API = "https://api.riopay.online/v1"

_PAID = {"COMPLETED"}
_CLOSED = {"CANCELED", "CANCELLED", "FAILED", "EXPIRED"}


class RiopayGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.RIOPAY

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _token(self) -> str:
        token = str(self.settings.get("api_token") or "")
        if not token:
            raise PaymentError("RioPay: api_token not configured")
        return token

    def _webhook_secret(self) -> str:
        return str(self.settings.get("webhook_secret") or "") or self._token()

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        payload: dict[str, Any] = {
            "amount": str((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "externalId": str(ctx.payment_id),
            "purpose": (ctx.description or "VPN subscription")[:128],
        }
        if ctx.return_url:
            payload["successUrl"] = ctx.return_url
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/orders", json=payload, headers={"X-Api-Token": self._token()}
            )
        data = res.json() if res.status_code in (200, 201) else {}
        url = str(data.get("paymentLink") or "")
        if res.status_code not in (200, 201) or not url:
            log.error("riopay create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"RioPay error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(data.get("id") or ""),
            redirect_url=url,
        )

    def _map(self, body: dict[str, Any]) -> WebhookResult:
        status_raw = str(body.get("status") or "").upper()
        if status_raw in _PAID:
            status = TransactionStatus.COMPLETED
        elif status_raw in _CLOSED:
            status = TransactionStatus.CANCELED
        else:
            status = TransactionStatus.PENDING
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(str(body.get("externalId") or ""))
        amount = None
        with contextlib.suppress(ArithmeticError, TypeError):
            amount = Money(int(Decimal(str(body.get("amount") or "0")) * 100), Currency.RUB)
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(body.get("id") or "") or None,
            amount=amount,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        headers = {k.lower(): v for k, v in request.headers.items()}
        signature = str(headers.get("x-signature") or "")
        expected = hmac.new(
            self._webhook_secret().encode(), request.body, hashlib.sha512
        ).hexdigest()
        if not signature or not hmac.compare_digest(expected.lower(), signature.lower()):
            raise WebhookVerificationError("RioPay: signature mismatch")
        return self._map(self.parse_json(request.body))

    async def fetch_status(self, external_id: str) -> WebhookResult | None:
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                res = await http.get(
                    f"{API}/orders/{external_id}", headers={"X-Api-Token": self._token()}
                )
        except httpx.HTTPError:
            return None
        if res.status_code != 200:
            return None
        result = self._map(res.json())
        return result if result.status is not TransactionStatus.PENDING else None
