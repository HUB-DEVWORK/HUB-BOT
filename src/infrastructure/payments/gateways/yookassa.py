"""YooKassa gateway (API v3): hosted redirect payment + webhook.

Create: POST /v3/payments with Basic(shop_id, secret_key) and an Idempotence-Key equal
to our ``payment_id`` — retries can never double-charge. Webhook carries no signature,
so we verify by REFETCHING the payment from the YooKassa API and trusting only that
response (stronger than IP allowlists behind proxies).

Settings row keys: ``shop_id``, ``secret_key`` (Fernet-encrypted at rest),
optional ``return_url``.
"""

from __future__ import annotations

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

API = "https://api.yookassa.ru/v3"

_STATUS_MAP = {
    "succeeded": TransactionStatus.COMPLETED,
    "canceled": TransactionStatus.CANCELED,
    "waiting_for_capture": TransactionStatus.PENDING,
    "pending": TransactionStatus.PENDING,
}


class YookassaGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.YOOKASSA

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            currencies=frozenset({Currency.RUB}),
            needs_http_webhook=True,
            supports_refund=True,
        )

    def _auth(self) -> tuple[str, str]:
        shop_id = str(self.settings.get("shop_id") or "")
        secret = str(self.settings.get("secret_key") or "")
        if not shop_id or not secret:
            raise PaymentError("YooKassa: shop_id/secret_key not configured")
        return (shop_id, secret)

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        value = (Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))
        payload: dict[str, Any] = {
            "amount": {"value": str(value), "currency": ctx.amount.currency.value},
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": ctx.return_url
                or str(self.settings.get("return_url") or "https://t.me"),
            },
            "description": ctx.description[:128] or "VPN subscription",
            "metadata": {"payment_id": str(ctx.payment_id)},
        }
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/payments",
                json=payload,
                auth=self._auth(),
                headers={"Idempotence-Key": str(ctx.payment_id)},
            )
        if res.status_code not in (200, 201):
            log.error("yookassa create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"YooKassa error {res.status_code}")
        data = res.json()
        url = (data.get("confirmation") or {}).get("confirmation_url")
        if not url:
            raise PaymentError("YooKassa: no confirmation_url in response")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT, external_id=str(data["id"]), redirect_url=url
        )

    @staticmethod
    def _map_payment(data: dict[str, Any]) -> WebhookResult:
        status = _STATUS_MAP.get(str(data.get("status")), TransactionStatus.PENDING)
        payment_id: UUID | None = None
        meta_pid = (data.get("metadata") or {}).get("payment_id")
        if meta_pid:
            try:
                payment_id = UUID(str(meta_pid))
            except ValueError:
                payment_id = None
        amount = None
        raw_amount = (data.get("amount") or {}).get("value")
        if raw_amount:
            amount = Money(int(Decimal(str(raw_amount)) * 100), Currency.RUB)
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(data.get("id") or "") or None,
            amount=amount,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        body = self.parse_json(request.body)
        obj = body.get("object") or {}
        external_id = str(obj.get("id") or "")
        if not external_id:
            raise WebhookVerificationError("YooKassa: no payment id in webhook")

        # The webhook is unsigned: refetch the payment and trust ONLY the API response.
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                res = await http.get(f"{API}/payments/{external_id}", auth=self._auth())
        except httpx.HTTPError as exc:
            # Non-2xx makes YooKassa resend the webhook later — no payment is lost.
            raise WebhookVerificationError(f"YooKassa: payment refetch failed: {exc}") from exc
        if res.status_code != 200:
            raise WebhookVerificationError(f"YooKassa: payment refetch failed {res.status_code}")
        return self._map_payment(res.json())

    async def fetch_status(self, external_id: str) -> WebhookResult | None:
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                res = await http.get(f"{API}/payments/{external_id}", auth=self._auth())
        except httpx.HTTPError:
            return None
        if res.status_code != 200:
            return None
        return self._map_payment(res.json())
