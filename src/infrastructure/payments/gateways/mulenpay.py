"""MulenPay (mulenpay.ru) — card/SBP payments, sha1-signed both ways.

Create: POST /api/v2/payments (Bearer api_key) with body field
``sign = sha1(currency + amount + shop_id + secret_key)``; ``uuid`` = our payment_id.
Webhook: JSON with ``sign`` inside the body —
``sha1(concat(values except sign) + secret_key)`` over the values in body order.

Settings row keys: ``api_key``, ``shop_id``, ``secret_key`` (Fernet-encrypted at rest).
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
from src.infrastructure.payments.base import BasePaymentGateway

log = get_logger(__name__)

API = "https://mulenpay.ru/api"

_PAID = {"paid", "success", "succeeded", "3"}
_CLOSED = {"canceled", "cancelled", "fail", "failed", "expired"}


class MulenpayGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.MULENPAY

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _creds(self) -> tuple[str, str, str]:
        api_key = str(self.settings.get("api_key") or "")
        shop_id = str(self.settings.get("shop_id") or "")
        secret = str(self.settings.get("secret_key") or "")
        if not api_key or not shop_id or not secret:
            raise PaymentError("MulenPay: api_key/shop_id/secret_key not configured")
        return api_key, shop_id, secret

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        api_key, shop_id, secret = self._creds()
        amount = f"{ctx.amount.amount_minor / 100:.2f}"
        currency = "rub"
        sign = hashlib.sha1(f"{currency}{amount}{shop_id}{secret}".encode()).hexdigest()
        payload: dict[str, Any] = {
            "currency": currency,
            "amount": amount,
            "uuid": str(ctx.payment_id),
            "shopId": shop_id,
            "description": (ctx.description or "VPN subscription")[:128],
            "items": [],
            "language": "ru",
            "sign": sign,
        }
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/v2/payments",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        data = res.json() if res.status_code in (200, 201) else {}
        url = str(data.get("paymentUrl") or data.get("payment_url") or data.get("url") or "")
        if not data.get("success") or not url:
            log.error("mulenpay create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"MulenPay error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(data.get("id") or ctx.payment_id),
            redirect_url=url,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        _, _, secret = self._creds()
        body = self.parse_json(request.body)
        received = str(body.pop("sign", "") or "")
        joined = "".join(str(v) for v in body.values())
        expected = hashlib.sha1((joined + secret).encode()).hexdigest()
        if not received or not hmac.compare_digest(received.lower(), expected.lower()):
            raise WebhookVerificationError("MulenPay: signature mismatch")

        status_raw = str(body.get("status") or "").lower()
        if status_raw in _PAID:
            status = TransactionStatus.COMPLETED
        elif status_raw in _CLOSED:
            status = TransactionStatus.CANCELED
        else:
            status = TransactionStatus.PENDING
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(str(body.get("uuid") or ""))
        from src.core.money import Money

        amount = None
        with contextlib.suppress(ArithmeticError, TypeError):
            amount = Money(int(Decimal(str(body.get("amount") or "0")) * 100), Currency.RUB)
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(body.get("id") or "") or None,
            amount=amount,
        )
