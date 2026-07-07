"""CloudPayments — card orders via /orders/create, HMAC-signed notifications.

Create: POST https://api.cloudpayments.ru/orders/create with Basic(public_id, api_secret),
``InvoiceId`` = our payment_id; the response Model.Url is the hosted payment page.
Pay-notification: form-encoded, signature is base64 HMAC-SHA256 of the body with the
api_secret — CloudPayments sends Content-HMAC (raw body) and X-Content-HMAC
(URL-decoded body), we accept either. The endpoint must answer ``{"code":0}``.

Settings row keys: ``public_id``, ``api_secret`` (Fernet-encrypted at rest).
Refunds: POST /payments/refund with the transaction id.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, unquote_plus
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

API = "https://api.cloudpayments.ru"


class CloudpaymentsGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.CLOUDPAYMENTS

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            currencies=frozenset({Currency.RUB}), needs_http_webhook=True, supports_refund=True
        )

    def _auth(self) -> tuple[str, str]:
        public_id = str(self.settings.get("public_id") or "")
        secret = str(self.settings.get("api_secret") or "")
        if not public_id or not secret:
            raise PaymentError("CloudPayments: public_id/api_secret not configured")
        return public_id, secret

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        payload: dict[str, Any] = {
            "Amount": float((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "Currency": ctx.amount.currency.value,
            "Description": (ctx.description or "VPN subscription")[:128],
            "AccountId": str(ctx.user_id),
            "InvoiceId": str(ctx.payment_id),
            "SuccessRedirectUrl": ctx.return_url or "https://t.me",
        }
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(f"{API}/orders/create", json=payload, auth=self._auth())
        data = res.json() if res.status_code == 200 else {}
        model = data.get("Model") or {}
        if not data.get("Success") or not model.get("Url"):
            log.error("cloudpayments create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"CloudPayments error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(model.get("Id") or ctx.payment_id),
            redirect_url=str(model["Url"]),
        )

    def _verify(self, request: WebhookRequest) -> None:
        _, secret = self._auth()
        headers = {k.lower(): v for k, v in request.headers.items()}
        signature = headers.get("content-hmac") or headers.get("x-content-hmac") or ""
        if not signature:
            raise WebhookVerificationError("CloudPayments: no HMAC header")

        def calc(data: bytes) -> str:
            return base64.b64encode(
                hmac.new(secret.encode(), data, hashlib.sha256).digest()
            ).decode()

        if hmac.compare_digest(calc(request.body), signature):
            return
        decoded = unquote_plus(request.body.decode("utf-8", "replace")).encode()
        if hmac.compare_digest(calc(decoded), signature):
            return
        raise WebhookVerificationError("CloudPayments: HMAC mismatch")

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        self._verify(request)
        f = dict(parse_qsl(request.body.decode("utf-8", "replace")))
        status_raw = str(f.get("Status") or "").lower()
        if status_raw in ("completed", "authorized"):
            status = TransactionStatus.COMPLETED
        elif status_raw in ("declined", "cancelled", "canceled"):
            status = TransactionStatus.CANCELED
        else:
            status = TransactionStatus.PENDING
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(str(f.get("InvoiceId") or ""))
        amount = None
        with contextlib.suppress(ArithmeticError):
            amount = Money(int(Decimal(str(f.get("Amount") or "0")) * 100), Currency.RUB)
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(f.get("TransactionId") or "") or None,
            amount=amount,
            http_body='{"code":0}',  # CloudPayments retries unless it sees code 0
        )

    async def refund(self, external_id: str, amount: Money) -> bool:
        payload = {
            "TransactionId": int(external_id),
            "Amount": float((Decimal(amount.amount_minor) / 100).quantize(Decimal("0.01"))),
        }
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(f"{API}/payments/refund", json=payload, auth=self._auth())
        ok = res.status_code == 200 and bool((res.json() or {}).get("Success"))
        if not ok:
            log.error("cloudpayments refund failed", status=res.status_code, body=res.text[:300])
        return ok
