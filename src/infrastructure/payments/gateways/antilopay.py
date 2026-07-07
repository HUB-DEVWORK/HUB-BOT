"""Antilopay (lk.antilopay.com v2) — RU acquiring (SBP/card/SberPay), RSA-signed.

Create: POST /api/v2/payment/create. We sign the EXACT compact JSON body with
SHA256-RSA (PKCS#1 v1.5) using our private key and put base64 into ``X-Apay-Sign``
(``X-Apay-Sign-Version: 1``, ``X-Apay-Secret-Id`` header). Success is HTTP 200 with
``code == 0`` -> ``payment_url``. Webhook: RSA-SHA256 signature in ``X-Apay-Callback``
over the raw body, verified with Antilopay's PUBLIC key. Statuses: SUCCESS paid,
FAIL/CANCEL/EXPIRED/CHARGEBACK/REVERSED closed. We trust ``original_amount`` (pre-fee).

Settings row keys: ``secret_id``, ``project_id`` (project_identificator),
``private_key`` (base64 DER PKCS8), ``public_key`` (PEM, for webhook verify),
optional ``prefer_method`` (SBP|CARD_RU|SBER_PAY). All Fernet-encrypted at rest.
"""

from __future__ import annotations

import base64
import contextlib
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

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

API = "https://lk.antilopay.com/api/v2"

_PAID = {"SUCCESS"}
_CLOSED = {"FAIL", "CANCEL", "CANCELLED", "CANCELED", "EXPIRED", "CHARGEBACK", "REVERSED"}


class AntilopayGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.ANTILOPAY

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _sign_body(self, body: bytes) -> str:
        raw = str(self.settings.get("private_key") or "")
        if not raw:
            raise PaymentError("Antilopay: private_key not configured")
        key = serialization.load_der_private_key(base64.b64decode(raw), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise PaymentError("Antilopay: private_key is not an RSA key")
        signature = key.sign(body, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode()

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        secret_id = str(self.settings.get("secret_id") or "")
        project = str(self.settings.get("project_id") or "")
        if not secret_id or not project:
            raise PaymentError("Antilopay: secret_id/project_id not configured")
        method = str(self.settings.get("prefer_method") or "SBP").upper()
        payload: dict[str, Any] = {
            "project_identificator": project,
            "amount": float((Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))),
            "order_id": str(ctx.payment_id),
            "currency": ctx.amount.currency.value.lower(),
            "product_name": (ctx.description or "VPN subscription")[:128],
            "product_type": "services",
            "description": (ctx.description or "VPN subscription")[:128],
            "customer": {"email": f"{ctx.telegram_id or ctx.user_id}@vpn.bot"},
            "prefer_methods": [method],
            "merchant_extra": str(ctx.payment_id),
        }
        if ctx.return_url:
            payload["success_url"] = ctx.return_url
            payload["fail_url"] = ctx.return_url
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/payment/create",
                content=body,
                headers={
                    "X-Apay-Secret-Id": secret_id,
                    "X-Apay-Sign": self._sign_body(body),
                    "X-Apay-Sign-Version": "1",
                    "Content-Type": "application/json",
                },
            )
        data = res.json() if res.status_code == 200 else {}
        url = str(data.get("payment_url") or "")
        if data.get("code") != 0 or not url:
            log.error("antilopay create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"Antilopay error {res.status_code}")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(data.get("payment_id") or ""),
            redirect_url=url,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        pem = str(self.settings.get("public_key") or "")
        if not pem:
            raise WebhookVerificationError("Antilopay: public_key not configured")
        headers = {k.lower(): v for k, v in request.headers.items()}
        signature = str(headers.get("x-apay-callback") or "")
        if not signature:
            raise WebhookVerificationError("Antilopay: no X-Apay-Callback header")
        try:
            key = serialization.load_pem_public_key(pem.encode())
            if not isinstance(key, RSAPublicKey):
                raise TypeError("expected RSA public key")
            key.verify(
                base64.b64decode(signature), request.body, padding.PKCS1v15(), hashes.SHA256()
            )
        except Exception as exc:
            raise WebhookVerificationError("Antilopay: signature verification failed") from exc

        body = self.parse_json(request.body)
        status_raw = str(body.get("status") or "").upper()
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
        # original_amount is the pre-fee sum in rubles — that is what the buyer owes us.
        raw_amount = body.get("original_amount") or body.get("amount")
        with contextlib.suppress(ArithmeticError, TypeError):
            amount = Money(int(Decimal(str(raw_amount or "0")) * 100), Currency.RUB)
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(body.get("payment_id") or "") or None,
            amount=amount,
        )
