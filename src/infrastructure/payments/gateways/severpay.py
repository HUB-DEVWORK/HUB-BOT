"""SeverPay (severpay.io) — card/SBP, HMAC-SHA256 over sorted compact JSON both ways.

Create: POST /api/merchant/payin/create with the signature IN the body. The signature
adds ``mid`` + a per-request ``salt``, drops ``sign``, sorts keys, compacts the JSON
(ensure_ascii=False) and takes HMAC-SHA256(token) hex. ``order_id`` = our payment_id.
Webhook uses the same formula. Statuses: success paid, decline/fail closed.

Settings row keys: ``token``, ``mid`` (merchant id, Fernet-encrypted at rest).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import uuid as uuid_mod
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

API = "https://severpay.io/api/merchant"

_PAID = {"success"}
_CLOSED = {"decline", "fail", "failed", "cancelled", "canceled"}


class SeverpayGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.SEVERPAY

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _creds(self) -> tuple[str, int]:
        token = str(self.settings.get("token") or "")
        mid = str(self.settings.get("mid") or "")
        if not token or not mid:
            raise PaymentError("SeverPay: token/mid not configured")
        return token, int(mid) if mid.isdigit() else 0

    def _sign(self, payload: dict[str, Any], token: str) -> str:
        data = {k: v for k, v in payload.items() if k != "sign"}
        canonical = json.dumps(
            {k: data[k] for k in sorted(data)}, ensure_ascii=False, separators=(",", ":")
        )
        return hmac.new(token.encode(), canonical.encode(), hashlib.sha256).hexdigest()

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        token, mid = self._creds()
        payload: dict[str, Any] = {
            "order_id": str(ctx.payment_id),
            "amount": float((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "currency": ctx.amount.currency.value,
            "client_email": f"{ctx.telegram_id or ctx.user_id}@telegram.org",
            "client_id": str(ctx.user_id),
            "url_return": ctx.return_url or "https://t.me",
            "lifetime": 1440,
            "mid": mid,
            "salt": uuid_mod.uuid4().hex,
        }
        payload["sign"] = self._sign(payload, token)
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(f"{API}/payin/create", json=payload)
        data = res.json() if res.status_code == 200 else {}
        inner = data.get("data") or {}
        if not data.get("status") or not inner.get("url"):
            log.error("severpay create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"SeverPay error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(inner.get("id") or inner.get("uid") or ""),
            redirect_url=str(inner["url"]),
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        token, _ = self._creds()
        body = self.parse_json(request.body)
        received = str(body.get("sign") or "")
        expected = self._sign(body, token)
        if not received or not hmac.compare_digest(received.lower(), expected.lower()):
            raise WebhookVerificationError("SeverPay: signature mismatch")

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
            external_id=str(body.get("id") or "") or None,
            amount=amount,
        )
