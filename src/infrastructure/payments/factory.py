"""GatewayFactory — maps PaymentGatewayType to its class (ADR-0004).

Registration is a single dict entry. The webhook route and processing pipeline are
gateway-agnostic, so adding a provider never touches them.
"""

from __future__ import annotations

from typing import Any

from src.core.enums import PaymentGatewayType
from src.core.exceptions import GatewayNotConfigured
from src.infrastructure.payments.base import BasePaymentGateway
from src.infrastructure.payments.gateways.manual import ManualGateway
from src.infrastructure.payments.gateways.telegram_stars import TelegramStarsGateway

# Register a new provider here (plus a core.enums value and a DB seed row).
_REGISTRY: dict[PaymentGatewayType, type[BasePaymentGateway]] = {
    PaymentGatewayType.MANUAL: ManualGateway,
    PaymentGatewayType.TELEGRAM_STARS: TelegramStarsGateway,
}


class GatewayFactory:
    """Constructs a gateway instance from its type and (decrypted) settings."""

    def __init__(self, registry: dict[PaymentGatewayType, type[BasePaymentGateway]] | None = None):
        self._registry = registry or dict(_REGISTRY)

    def supported(self) -> frozenset[PaymentGatewayType]:
        return frozenset(self._registry)

    def create(
        self, gateway_type: PaymentGatewayType, settings: dict[str, Any]
    ) -> BasePaymentGateway:
        cls = self._registry.get(gateway_type)
        if cls is None:
            raise GatewayNotConfigured(f"no gateway registered for {gateway_type.value}")
        return cls(settings)
