"""Cryptomus — crypto invoice priced in fiat (RUB), hosted redirect.

Sign scheme (both requests and webhook): ``md5(base64(compact_json) + api_key)`` with
compact, ensure_ascii=False JSON — exactly PHP's json_encode(JSON_UNESCAPED_UNICODE).
The webhook carries ``sign`` INSIDE the body: pop it, re-serialize compactly, compare.
``order_id`` = our payment_id.

Settings row keys: ``merchant_uuid``, ``api_key`` (Fernet-encrypted at rest).
Heleket is a full clone of this scheme (see heleket.py).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any, ClassVar
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

_PAID = {"paid", "paid_over"}
_CLOSED = {"cancel", "fail", "system_fail", "refund_process", "refund_paid", "wrong_amount"}


def _compact(data: dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


class CryptomusGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.CRYPTOMUS
    api_base: ClassVar[str] = "https://api.cryptomus.com/v1"
    title: ClassVar[str] = "Cryptomus"

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            currencies=frozenset({Currency.RUB}), needs_http_webhook=True, supports_refund=True
        )

    def _creds(self) -> tuple[str, str]:
        merchant = str(self.settings.get("merchant_uuid") or "")
        key = str(self.settings.get("api_key") or "")
        if not merchant or not key:
            raise PaymentError(f"{self.title}: merchant_uuid/api_key not configured")
        return merchant, key

    def _sign(self, body: bytes) -> str:
        _, key = self._creds()
        return hashlib.md5(base64.b64encode(body) + key.encode()).hexdigest()

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        merchant, _ = self._creds()
        payload: dict[str, Any] = {
            "amount": str((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "currency": ctx.amount.currency.value,
            "order_id": str(ctx.payment_id),
            "lifetime": 3600,
            "url_return": ctx.return_url or "https://t.me",
        }
        body = _compact(payload)
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{self.api_base}/payment",
                content=body,
                headers={
                    "merchant": merchant,
                    "sign": self._sign(body),
                    "Content-Type": "application/json",
                },
            )
        data = res.json() if res.status_code == 200 else {}
        result = data.get("result") or {}
        if not result.get("url"):
            log.error(
                f"{self.title.lower()} create failed", status=res.status_code, body=res.text[:300]
            )
            raise PaymentError(f"{self.title} error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(result.get("uuid") or ""),
            redirect_url=str(result["url"]),
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        body = self.parse_json(request.body)
        got = str(body.pop("sign", "") or "")
        expected = self._sign(_compact(body))
        if not got or not hmac.compare_digest(got.lower(), expected.lower()):
            raise WebhookVerificationError(f"{self.title}: signature mismatch")

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
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(body.get("uuid") or "") or None,
        )

    async def refund(self, external_id: str, amount: Money) -> bool:
        """POST /v1/payment/refund — full refund to the payer's address."""
        merchant, _ = self._creds()
        payload = _compact({"uuid": external_id, "is_subtract": True})
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{self.api_base}/payment/refund",
                content=payload,
                headers={
                    "merchant": merchant,
                    "sign": self._sign(payload),
                    "Content-Type": "application/json",
                },
            )
        ok = res.status_code == 200 and (res.json() or {}).get("result") is not None
        if not ok:
            log.error(
                f"{self.title.lower()} refund failed", status=res.status_code, body=res.text[:300]
            )
        return ok

    async def fetch_status(self, external_id: str) -> WebhookResult | None:
        merchant, _ = self._creds()
        payload = _compact({"uuid": external_id})
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                res = await http.post(
                    f"{self.api_base}/payment/info",
                    content=payload,
                    headers={
                        "merchant": merchant,
                        "sign": self._sign(payload),
                        "Content-Type": "application/json",
                    },
                )
        except httpx.HTTPError:
            return None
        if res.status_code != 200:
            return None
        result = res.json().get("result") or {}
        status_raw = str(result.get("payment_status") or result.get("status") or "").lower()
        if status_raw in _PAID:
            status = TransactionStatus.COMPLETED
        elif status_raw in _CLOSED:
            status = TransactionStatus.CANCELED
        else:
            return None
        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(str(result.get("order_id") or ""))
        return WebhookResult(status=status, payment_id=payment_id, external_id=external_id)
