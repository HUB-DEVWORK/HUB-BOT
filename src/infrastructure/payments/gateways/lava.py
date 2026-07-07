"""Lava Business (api.lava.ru) — card/SBP invoices, HMAC-signed both ways.

Requests: HMAC-SHA256 hex of the RAW body with ``secret_key`` in the ``Signature``
header. Create: POST /business/invoice/create {sum, orderId, shopId, ...} ->
{"status":"success","data":{url,...}}. Webhook: signature arrives in the
``Authorization`` header, HMAC with ``webhook_secret`` over the raw body (legacy shops
sign a re-serialized sorted-keys JSON — both variants are accepted, the same gotcha
the battle-tested integration hit). ``order_id`` = our payment_id.

Settings row keys: ``shop_id``, ``secret_key``, ``webhook_secret``
(Fernet-encrypted at rest).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
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

API = "https://api.lava.ru"

_PAID = {"success", "paid"}
_CLOSED = {"cancel", "cancelled", "canceled", "expired", "error", "failed"}


class LavaGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.LAVA

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _creds(self) -> tuple[str, str]:
        shop = str(self.settings.get("shop_id") or "")
        secret = str(self.settings.get("secret_key") or "")
        if not shop or not secret:
            raise PaymentError("Lava: shop_id/secret_key not configured")
        return shop, secret

    @staticmethod
    def _hmac_hex(message: bytes, key: str) -> str:
        return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        shop, secret = self._creds()
        payload: dict[str, Any] = {
            "sum": float((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "orderId": str(ctx.payment_id),
            "shopId": shop,
            "comment": (ctx.description or "VPN subscription")[:255],
        }
        if ctx.return_url:
            payload["successUrl"] = ctx.return_url[:500]
        body = json.dumps(payload).encode()
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/business/invoice/create",
                content=body,
                headers={
                    "Signature": self._hmac_hex(body, secret),
                    "Content-Type": "application/json",
                },
            )
        data = res.json() if res.status_code == 200 else {}
        inner = data.get("data") or {}
        url = str(inner.get("url") or inner.get("paymentUrl") or "")
        if str(data.get("status") or "").lower() != "success" or not url:
            log.error("lava create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"Lava error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(inner.get("id") or ctx.payment_id),
            redirect_url=url,
        )

    def _verify(self, request: WebhookRequest) -> dict[str, Any]:
        secret = str(self.settings.get("webhook_secret") or "")
        if not secret:
            raise WebhookVerificationError("Lava: webhook_secret not configured")
        headers = {k.lower(): v for k, v in request.headers.items()}
        received = (headers.get("authorization") or headers.get("signature") or "").strip()
        if not received:
            raise WebhookVerificationError("Lava: no signature header")
        if hmac.compare_digest(self._hmac_hex(request.body, secret).lower(), received.lower()):
            return self.parse_json(request.body)
        # legacy PHP SDK shops: HMAC over sorted-keys re-serialized JSON
        body = self.parse_json(request.body)
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        if hmac.compare_digest(self._hmac_hex(canonical, secret).lower(), received.lower()):
            return body
        raise WebhookVerificationError("Lava: signature mismatch")

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        body = self._verify(request)
        status_raw = str(body.get("status") or "").lower()
        if status_raw in _PAID:
            status = TransactionStatus.COMPLETED
        elif status_raw in _CLOSED:
            status = TransactionStatus.CANCELED
        else:
            status = TransactionStatus.PENDING
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(str(body.get("order_id") or body.get("orderId") or ""))
        amount = None
        with contextlib.suppress(ArithmeticError, TypeError):
            amount = Money(
                int(Decimal(str(body.get("amount") or body.get("sum") or "0")) * 100), Currency.RUB
            )
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(body.get("invoice_id") or body.get("id") or "") or None,
            amount=amount,
        )
