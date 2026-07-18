"""YooKassa gateway (API v3): hosted redirect payment + webhook.

Create: POST /v3/payments with Basic(shop_id, secret_key) and an Idempotence-Key equal
to our ``payment_id`` — retries can never double-charge. Webhook carries no signature,
so we verify by REFETCHING the payment from the YooKassa API and trusting only that
response (stronger than IP allowlists behind proxies).

Settings row keys: ``shop_id``, ``secret_key`` (Fernet-encrypted at rest),
optional ``return_url``, optional ``recurrent_enabled`` — when on, payments are created
with ``save_payment_method`` so the card can be charged later without the user
(``charge_saved``, used by the autopay task).
"""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from src.application.common.payments import (
    GatewayCapabilities,
    PaymentContext,
    PaymentResult,
    PaymentResultKind,
    SavedPaymentMethod,
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

# The webhook is unsigned and public: its ``object.id`` is fed into the path of an authenticated
# GET to the YooKassa API. Real payment ids are UUIDs (hex + hyphen), so restrict to that charset —
# it admits no ``.``, ``/`` or spaces, which blocks path injection (``../``, extra segments) into
# that authenticated request. Length is bounded generously to tolerate id-format drift.
_PAYMENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{4,64}$")

_STATUS_MAP = {
    "succeeded": TransactionStatus.COMPLETED,
    "canceled": TransactionStatus.CANCELED,
    "waiting_for_capture": TransactionStatus.PENDING,
    "pending": TransactionStatus.PENDING,
}

# Sleeps between webhook-refetch attempts (transport error / 5xx from the YooKassa API).
_REFETCH_BACKOFF: tuple[float, ...] = (0.5, 2.0)


class YookassaGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.YOOKASSA

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            currencies=frozenset({Currency.RUB}),
            needs_http_webhook=True,
            supports_refund=True,
            supports_recurrent=True,
            supports_saved_method=True,
        )

    @property
    def recurrent_enabled(self) -> bool:
        """Admin opted the shop into saved-card charges (requires recurring enabled
        on the YooKassa side too — otherwise create_payment would 400)."""
        enabled = str(self.settings.get("recurrent_enabled") or "").lower()
        return enabled in ("1", "true", "yes", "on")

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
        if self.recurrent_enabled:
            payload["save_payment_method"] = True
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

    async def refund(self, external_id: str, amount: Money) -> bool:
        """POST /v3/refunds — idempotent per payment (one full refund per transaction)."""
        value = (Decimal(amount.amount_minor) / 100).quantize(Decimal("0.01"))
        payload = {
            "payment_id": external_id,
            "amount": {"value": str(value), "currency": amount.currency.value},
        }
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"{API}/refunds",
                json=payload,
                auth=self._auth(),
                headers={"Idempotence-Key": f"refund-{external_id}"},
            )
        ok = res.status_code in (200, 201) and res.json().get("status") in (
            "succeeded",
            "pending",
        )
        if not ok:
            log.error("yookassa refund failed", status=res.status_code, body=res.text[:300])
        return ok

    async def charge_saved(self, ctx: PaymentContext, payment_method_id: str) -> WebhookResult:
        """Merchant-initiated charge on a saved card: no ``confirmation`` step.

        The response IS the payment state (usually terminal right away) — mapped exactly
        like a webhook refetch so the caller can feed it into the standard pipeline.
        """
        value = (Decimal(ctx.amount.amount_minor) / 100).quantize(Decimal("0.01"))
        payload: dict[str, Any] = {
            "amount": {"value": str(value), "currency": ctx.amount.currency.value},
            "capture": True,
            "payment_method_id": payment_method_id,
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
            log.error("yookassa charge_saved failed", status=res.status_code, body=res.text[:300])
            raise PaymentError(f"YooKassa error {res.status_code}")
        return self._map_payment(res.json())

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
        saved_method = None
        pm = data.get("payment_method") or {}
        if pm.get("saved") and pm.get("id"):
            card = pm.get("card") or {}
            title = str(pm.get("title") or "") or (
                f"{card.get('card_type', 'Card')} *{card['last4']}" if card.get("last4") else None
            )
            saved_method = SavedPaymentMethod(method_id=str(pm["id"]), title=title)
        return WebhookResult(
            status=status,
            payment_id=payment_id,
            external_id=str(data.get("id") or "") or None,
            amount=amount,
            saved_method=saved_method,
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        body = self.parse_json(request.body)
        obj = body.get("object") or {}
        external_id = str(obj.get("id") or "")
        if not external_id:
            raise WebhookVerificationError("YooKassa: no payment id in webhook")
        if not _PAYMENT_ID_RE.match(external_id):
            # Reject before it reaches the authenticated GET's URL path (anti path-injection).
            raise WebhookVerificationError("YooKassa: malformed payment id")

        # The webhook is unsigned: refetch the payment and trust ONLY the API response.
        # A blip of the YooKassa API here used to 403 the webhook outright, parking the
        # payment until the reconciler (minutes of customer wait) — so retry transport
        # errors and 5xx briefly before giving up. 4xx won't heal and rejects at once.
        res: httpx.Response | None = None
        last_exc: httpx.HTTPError | None = None
        for pause in (*_REFETCH_BACKOFF, None):
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    res = await http.get(f"{API}/payments/{external_id}", auth=self._auth())
            except httpx.HTTPError as exc:
                res, last_exc = None, exc
            if res is not None and res.status_code < 500:
                break
            if pause is not None:
                await asyncio.sleep(pause)
        if res is None:
            # Non-2xx makes YooKassa resend the webhook later — no payment is lost.
            raise WebhookVerificationError(
                f"YooKassa: payment refetch failed: {last_exc}"
            ) from last_exc
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
