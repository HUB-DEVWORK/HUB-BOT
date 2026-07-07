"""AuraPay (app.aurapay.tech) — SBP/card invoices, HMAC-SHA256 value-concat webhook.

Create: POST /invoice/create with ``X-ApiKey`` + ``X-ShopId`` headers; the response
carries ``payment_data.url``. Webhook: ``X-SIGNATURE`` = HMAC-SHA256(webhook_secret) of
the payload values concatenated in ALPHABETICAL key order (None -> ''), like PHP implode.
Statuses: PAID paid, PENDING/EXPIRED not.

Settings row keys: ``api_key``, ``shop_id``, ``webhook_secret`` (Fernet-encrypted at rest).
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

API = "https://app.aurapay.tech"


class AurapayGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.AURAPAY

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _creds(self) -> tuple[str, str, str]:
        api_key = str(self.settings.get("api_key") or "")
        shop_id = str(self.settings.get("shop_id") or "")
        secret = str(self.settings.get("webhook_secret") or "")
        if not api_key or not shop_id or not secret:
            raise PaymentError("AuraPay: api_key/shop_id/webhook_secret not configured")
        return api_key, shop_id, secret

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        api_key, shop_id, _ = self._creds()
        payload: dict[str, Any] = {
            "amount": float((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "order_id": str(ctx.payment_id),
            "comment": (ctx.description or "VPN subscription")[:128],
            "service": str(self.settings.get("service") or "sbp"),
            "custom_fields": f"user_id={ctx.user_id}",
            "lifetime": 60,
        }
        if ctx.return_url:
            payload["success_url"] = ctx.return_url
            payload["fail_url"] = ctx.return_url
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/invoice/create",
                json=payload,
                headers={"X-ApiKey": api_key, "X-ShopId": shop_id},
            )
        data = res.json() if res.status_code == 200 else {}
        url = str((data.get("payment_data") or {}).get("url") or "")
        if not url:
            log.error("aurapay create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"AuraPay error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(data.get("id") or ""),
            redirect_url=url,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        _, _, secret = self._creds()
        body = self.parse_json(request.body)
        headers = {k.lower(): v for k, v in request.headers.items()}
        received = str(headers.get("x-signature") or "")
        message = "".join(
            "" if body[k] is None else str(body[k]) for k in sorted(body) if k != "signature"
        )
        expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        if not received or not hmac.compare_digest(received.lower(), expected.lower()):
            raise WebhookVerificationError("AuraPay: signature mismatch")

        status_raw = str(body.get("status") or "").upper()
        if status_raw == "PAID":
            status = TransactionStatus.COMPLETED
        elif status_raw in ("EXPIRED", "CANCELLED", "CANCELED", "FAILED"):
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
            external_id=str(body.get("id") or "") or None,
            amount=amount,
        )
