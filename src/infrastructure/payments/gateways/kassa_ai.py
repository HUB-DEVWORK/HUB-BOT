"""KassaAI (api.fk.life) — the modern FreeKassa-family API.

Create: POST /v1/orders/create with ``signature`` = HMAC-SHA256(api_key) over the
values of the payload sorted by key and joined with ``|``; ``paymentId`` = our uuid,
``nonce`` keeps requests unique. The response carries ``location`` — the payment page.
Webhook is the classic FreeKassa form: ``SIGN = md5(shop_id:amount:secret2:order_id)``
and the mandatory plain-text «YES» ACK.

Settings row keys: ``shop_id``, ``api_key``, ``secret2`` (Fernet-encrypted at rest),
optional ``payment_system_id`` (``i`` — preselected payment system).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl
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

API = "https://api.fk.life/v1"


def _fk_amount(minor: int) -> str:
    value = Decimal(minor) / 100
    return (
        str(int(value))
        if value == value.to_integral_value()
        else str(value.quantize(Decimal("0.01")))
    )


class KassaAiGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.KASSA_AI

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _creds(self) -> tuple[str, str, str]:
        shop = str(self.settings.get("shop_id") or "")
        api_key = str(self.settings.get("api_key") or "")
        s2 = str(self.settings.get("secret2") or "")
        if not shop or not api_key or not s2:
            raise PaymentError("KassaAI: shop_id/api_key/secret2 not configured")
        return shop, api_key, s2

    @staticmethod
    def _sign(params: dict[str, Any], api_key: str) -> str:
        data = {k: v for k, v in params.items() if k != "signature"}
        msg = "|".join(str(data[k]) for k in sorted(data))
        return hmac.new(api_key.encode(), msg.encode(), hashlib.sha256).hexdigest()

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        shop, api_key, _ = self._creds()
        params: dict[str, Any] = {
            "shopId": int(shop) if shop.isdigit() else shop,
            "nonce": time.time_ns(),
            "paymentId": str(ctx.payment_id),
            "amount": float((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "currency": ctx.amount.currency.value,
        }
        ps = str(self.settings.get("payment_system_id") or "")
        if ps:
            params["i"] = int(ps) if ps.isdigit() else ps
        params["signature"] = self._sign(params, api_key)
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(f"{API}/orders/create", json=params)
        data = res.json() if res.status_code == 200 else {}
        url = str(data.get("location") or data.get("url") or "")
        if str(data.get("type") or "").lower() == "error" or not url:
            log.error("kassa.ai create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"KassaAI error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(data.get("orderId") or ctx.payment_id),
            redirect_url=url,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        shop, _, s2 = self._creds()
        f = dict(parse_qsl(request.body.decode("utf-8", "replace")))
        amount = str(f.get("AMOUNT") or "")
        order = str(f.get("MERCHANT_ORDER_ID") or "")
        with contextlib.suppress(ArithmeticError, ValueError):
            amount = _fk_amount(int(Decimal(amount) * 100))
        expected = hashlib.md5(f"{shop}:{amount}:{s2}:{order}".encode()).hexdigest()
        got = str(f.get("SIGN") or "").lower()
        if not got or not hmac.compare_digest(got, expected.lower()):
            raise WebhookVerificationError("KassaAI: signature mismatch")
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(order)
        amount_money = None
        with contextlib.suppress(ArithmeticError):
            amount_money = Money(int(Decimal(str(f.get("AMOUNT") or "0")) * 100), Currency.RUB)
        return WebhookResult(
            status=TransactionStatus.COMPLETED,
            payment_id=payment_id,
            external_id=str(f.get("intid") or "") or order or None,
            amount=amount_money,
            http_body="YES",
        )
