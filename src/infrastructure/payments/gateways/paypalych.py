"""PayPalych (pal24.pro) — card/SBP bills, Robokassa-style callback.

Create: POST /api/v1/bill/create (Bearer) with our uuid as ``order_id``; the response
carries ``bill_id`` + payment links (``link_page_url``/``link_url``). Callback is
form-encoded with ``SignatureValue = MD5(OutSum:InvId:token).upper()`` where InvId is
the bill id — the webhook matches by ``external_id``. Statuses: SUCCESS/OVERPAID paid,
FAIL/CANCELLED closed, NEW/PROCESS/UNDERPAID pending.

Settings row keys: ``api_token``, ``shop_id`` (Fernet-encrypted at rest),
optional ``signature_token`` (defaults to the api token).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl

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

API = "https://pal24.pro/api/v1"

_PAID = {"SUCCESS", "OVERPAID"}
_CLOSED = {"FAIL", "CANCELLED", "CANCELED"}


class PaypalychGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.PAYPALYCH

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _token(self) -> str:
        token = str(self.settings.get("api_token") or "")
        if not token:
            raise PaymentError("PayPalych: api_token not configured")
        return token

    def _sign_token(self) -> str:
        return str(self.settings.get("signature_token") or "") or self._token()

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        shop_id = str(self.settings.get("shop_id") or "")
        if not shop_id:
            raise PaymentError("PayPalych: shop_id not configured")
        payload: dict[str, Any] = {
            "amount": str((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "shop_id": shop_id,
            "order_id": str(ctx.payment_id),
            "currency_in": ctx.amount.currency.value,
            "type": "normal",
            "description": (ctx.description or "VPN subscription")[:128],
        }
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/bill/create",
                json=payload,
                headers={"Authorization": f"Bearer {self._token()}"},
            )
        data = res.json() if res.status_code == 200 else {}
        bill_id = str(data.get("bill_id") or "")
        url = str(data.get("link_page_url") or data.get("link_url") or "")
        if not bill_id or not url:
            log.error("paypalych create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"PayPalych error {res.status_code}")
        return PaymentResult(kind=PaymentResultKind.REDIRECT, external_id=bill_id, redirect_url=url)

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        f = dict(parse_qsl(request.body.decode("utf-8", "replace")))
        if not f:  # some setups deliver JSON
            f = {k: str(v) for k, v in self.parse_json(request.body).items()}
        out_sum = str(f.get("OutSum") or "")
        inv_id = str(f.get("InvId") or "")
        got = str(f.get("SignatureValue") or "").upper()
        expected = (
            hashlib.md5(f"{out_sum}:{inv_id}:{self._sign_token()}".encode()).hexdigest().upper()
        )
        if not got or not hmac.compare_digest(got, expected):
            raise WebhookVerificationError("PayPalych: signature mismatch")

        status_raw = str(f.get("Status") or "").upper()
        if status_raw in _PAID:
            status = TransactionStatus.COMPLETED
        elif status_raw in _CLOSED:
            status = TransactionStatus.CANCELED
        else:
            status = TransactionStatus.PENDING
        from src.core.money import Money

        amount = None
        with contextlib.suppress(ArithmeticError):
            amount = Money(int(Decimal(out_sum or "0") * 100), Currency.RUB)
        return WebhookResult(status=status, external_id=inv_id or None, amount=amount)

    async def fetch_status(self, external_id: str) -> WebhookResult | None:
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                res = await http.get(
                    f"{API}/bill/status",
                    params={"id": external_id},
                    headers={"Authorization": f"Bearer {self._token()}"},
                )
        except httpx.HTTPError:
            return None
        if res.status_code != 200:
            return None
        status_raw = str(res.json().get("status") or "").upper()
        if status_raw in _PAID:
            return WebhookResult(status=TransactionStatus.COMPLETED, external_id=external_id)
        if status_raw in _CLOSED:
            return WebhookResult(status=TransactionStatus.CANCELED, external_id=external_id)
        return None
