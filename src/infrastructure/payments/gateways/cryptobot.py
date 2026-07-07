"""CryptoBot (Crypto Pay API) gateway: fiat-denominated crypto invoice + webhook.

Create: POST https://pay.crypt.bot/api/createInvoice with the Crypto-Pay-API-Token
header; we issue a FIAT invoice (RUB) so the user pays the crypto equivalent of the
exact ruble price. ``payload`` carries our payment_id.

Webhook: header ``crypto-pay-api-signature`` = HMAC-SHA256(body, key=sha256(token)).
Proxies may rewrite the body, so we fall back to compact re-serialization (both
ensure_ascii variants) — battle-tested against real proxy behaviour.

Settings row keys: ``api_token`` (Fernet-encrypted at rest), optional ``asset``.
"""

from __future__ import annotations

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
from src.infrastructure.payments.base import BasePaymentGateway

log = get_logger(__name__)

API = "https://pay.crypt.bot/api"


class CryptobotGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.CRYPTOBOT

    @property
    def capabilities(self) -> GatewayCapabilities:
        # RUB only: we always issue fiat-denominated invoices (currency_type=fiat).
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _token(self) -> str:
        token = str(self.settings.get("api_token") or "")
        if not token:
            raise PaymentError("CryptoBot: api_token not configured")
        return token

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        value = (Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))
        payload: dict[str, Any] = {
            "currency_type": "fiat",
            "fiat": ctx.amount.currency.value,
            "amount": str(value),
            "description": (ctx.description or "VPN subscription")[:1024],
            "payload": str(ctx.payment_id),
            "expires_in": 3600,
        }
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/createInvoice",
                json=payload,
                headers={"Crypto-Pay-API-Token": self._token()},
            )
        data = res.json() if res.status_code == 200 else {}
        if not data.get("ok"):
            log.error("cryptobot create failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"CryptoBot error {res.status_code}")
        result = data["result"]
        url = (
            result.get("bot_invoice_url")
            or result.get("mini_app_invoice_url")
            or result.get("pay_url")
        )
        if not url:
            raise PaymentError("CryptoBot: no invoice url in response")
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=str(result["invoice_id"]),
            redirect_url=str(url),
        )

    def _verify_signature(self, body: bytes, signature: str) -> None:
        secret = hashlib.sha256(self._token().encode()).digest()

        def ok(check: bytes) -> bool:
            expected = hmac.new(secret, check, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature or "")

        if ok(body):
            return
        # Proxies rewrite bodies: retry against compact re-serializations (gotcha #6).
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError("CryptoBot: unparsable webhook body") from exc
        for ensure_ascii in (False, True):
            compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=ensure_ascii)
            if ok(compact.encode()):
                return
        raise WebhookVerificationError("CryptoBot: signature mismatch")

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        headers = {k.lower(): v for k, v in request.headers.items()}
        self._verify_signature(request.body, headers.get("crypto-pay-api-signature", ""))

        body = self.parse_json(request.body)
        if body.get("update_type") != "invoice_paid":
            # Verified but irrelevant update: report pending so nothing changes.
            return WebhookResult(status=TransactionStatus.PENDING)
        invoice = body.get("payload") or {}
        payment_id: UUID | None = None
        if invoice.get("payload"):
            try:
                payment_id = UUID(str(invoice["payload"]))
            except ValueError:
                payment_id = None
        return WebhookResult(
            status=TransactionStatus.COMPLETED,
            payment_id=payment_id,
            external_id=str(invoice.get("invoice_id") or "") or None,
        )

    async def fetch_status(self, external_id: str) -> WebhookResult | None:
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                res = await http.get(
                    f"{API}/getInvoices",
                    params={"invoice_ids": external_id},
                    headers={"Crypto-Pay-API-Token": self._token()},
                )
        except httpx.HTTPError:
            return None
        data = res.json() if res.status_code == 200 else {}
        if not data.get("ok"):
            return None
        items = (data.get("result") or {}).get("items") or []
        if not items:
            return None
        invoice = items[0]
        status = str(invoice.get("status") or "")
        if status not in ("paid", "expired"):
            return None  # still active — nothing to reconcile yet
        payment_id: UUID | None = None
        if invoice.get("payload"):
            try:
                payment_id = UUID(str(invoice["payload"]))
            except ValueError:
                payment_id = None
        return WebhookResult(
            status=TransactionStatus.COMPLETED if status == "paid" else TransactionStatus.CANCELED,
            payment_id=payment_id,
            external_id=str(invoice.get("invoice_id") or "") or None,
        )
