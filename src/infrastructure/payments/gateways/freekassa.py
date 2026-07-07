"""FreeKassa — card/SBP/wallets via the classic SCI form link.

Create is a signed link to pay.fk.money: ``s = md5(shop_id:amount:secret1:RUB:order_id)``
(amount collapses to int when whole — FreeKassa signs "199", not "199.00").
Webhook (ResultURL): form-encoded MERCHANT_ID/AMOUNT/MERCHANT_ORDER_ID with
``SIGN = md5(shop_id:amount:secret2:order_id)`` and the mandatory plain-text «YES» ACK.

Settings row keys: ``shop_id``, ``secret1``, ``secret2`` (Fernet-encrypted at rest).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode
from uuid import UUID

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

PAY_URL = "https://pay.fk.money/"


def _fk_amount(minor: int) -> str:
    value = Decimal(minor) / 100
    return (
        str(int(value))
        if value == value.to_integral_value()
        else str(value.quantize(Decimal("0.01")))
    )


class FreekassaGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.FREEKASSA

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.RUB}), needs_http_webhook=True)

    def _creds(self) -> tuple[str, str, str]:
        shop = str(self.settings.get("shop_id") or "")
        s1 = str(self.settings.get("secret1") or "")
        s2 = str(self.settings.get("secret2") or "")
        if not shop or not s1 or not s2:
            raise PaymentError("FreeKassa: shop_id/secret1/secret2 not configured")
        return shop, s1, s2

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        shop, s1, _ = self._creds()
        amount = _fk_amount(ctx.amount.amount_minor)
        order = str(ctx.payment_id)
        sign = hashlib.md5(f"{shop}:{amount}:{s1}:RUB:{order}".encode()).hexdigest()
        params = {"m": shop, "oa": amount, "currency": "RUB", "o": order, "s": sign}
        return PaymentResult(
            kind=PaymentResultKind.REDIRECT,
            external_id=order,
            redirect_url=f"{PAY_URL}?{urlencode(params)}",
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
            raise WebhookVerificationError("FreeKassa: signature mismatch")

        payment_id = None
        with contextlib.suppress(ValueError):
            payment_id = UUID(order)
        money = None
        with contextlib.suppress(ArithmeticError):
            money = Money(int(Decimal(str(f.get("AMOUNT") or "0")) * 100), Currency.RUB)
        return WebhookResult(
            status=TransactionStatus.COMPLETED,
            payment_id=payment_id,
            external_id=str(f.get("intid") or "") or order or None,
            amount=money,
            http_body="YES",  # mandatory exact plain-text ACK
        )
