"""Telegram Stars gateway. No HTTP webhook — confirmation is the in-bot ``successful_payment``
handler (gotcha #10). ``create_payment`` returns an in-bot invoice payload for the bot to send.
"""

from __future__ import annotations

from src.application.common.payments import (
    GatewayCapabilities,
    PaymentContext,
    PaymentResult,
    PaymentResultKind,
    WebhookRequest,
    WebhookResult,
)
from src.core.enums import Currency, PaymentGatewayType
from src.core.exceptions import WebhookVerificationError
from src.infrastructure.payments.base import BasePaymentGateway


class TelegramStarsGateway(BasePaymentGateway):
    gateway_type = PaymentGatewayType.TELEGRAM_STARS

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(currencies=frozenset({Currency.XTR}), needs_http_webhook=False)

    async def create_payment(self, ctx: PaymentContext) -> PaymentResult:
        # The bot sends this as a Telegram invoice (currency XTR). The `payload` ties the
        # successful_payment update back to our transaction.
        return PaymentResult(
            kind=PaymentResultKind.IN_BOT,
            external_id=str(ctx.payment_id),
            invoice_payload={
                "currency": Currency.XTR.value,
                "amount": ctx.amount.amount_minor,  # Stars are integer, exponent 0
                "title": ctx.description[:32] or "VPN subscription",
                "description": ctx.description or "VPN subscription",
                "payload": str(ctx.payment_id),
            },
        )

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        raise WebhookVerificationError(
            "Telegram Stars has no HTTP webhook — confirm via the in-bot successful_payment handler"
        )
