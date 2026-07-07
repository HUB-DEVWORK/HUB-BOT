"""BasePaymentGateway ABC + shared verification helpers (ADR-0004, docs/context/03).

Concrete gateways subclass this. Shared helpers cover the cross-provider concerns: HMAC
signatures, IP-allowlisting (Cloudflare-aware), and JSON body parsing with re-serialization
fallbacks (proxies rewrite bodies and break HMAC-over-raw-body).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from abc import ABC, abstractmethod
from typing import Any

import orjson

from src.application.common.payments import (
    GatewayCapabilities,
    PaymentContext,
    PaymentResult,
    WebhookRequest,
    WebhookResult,
)
from src.core.enums import PaymentGatewayType
from src.core.exceptions import PaymentError, WebhookVerificationError
from src.core.money import Money


class BasePaymentGateway(ABC):
    """One payment provider. Settings come pre-decrypted from the ``payment_gateways`` row."""

    gateway_type: PaymentGatewayType

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    @property
    @abstractmethod
    def capabilities(self) -> GatewayCapabilities: ...

    @abstractmethod
    async def create_payment(self, ctx: PaymentContext) -> PaymentResult: ...

    @abstractmethod
    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult: ...

    async def refund(self, external_id: str, amount: Money) -> bool:
        """Refund a completed payment at the provider. Default: not supported.

        Gateways with ``capabilities.supports_refund`` override this; everything else
        is refunded manually by the admin (the cabinet only records the outcome).
        """
        raise PaymentError(f"{self.gateway_type.value}: refunds via API are not supported")

    async def fetch_status(self, external_id: str) -> WebhookResult | None:
        """Poll the provider for a payment's current state (reconcile path).

        Returns ``None`` when the gateway cannot poll (default) or the state is not
        terminal yet. Used by the scheduled reconciler to recover payments whose
        webhook was lost or whose fulfilment failed mid-flight.
        """
        return None

    # --- shared helpers ---------------------------------------------------
    @staticmethod
    def verify_hmac(body: bytes, signature: str, secret: str, *, algo: str = "sha256") -> None:
        expected = hmac.new(secret.encode(), body, getattr(hashlib, algo)).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            raise WebhookVerificationError("signature mismatch")

    @staticmethod
    def client_ip(request: WebhookRequest, *, trust_proxy: bool = False) -> str | None:
        """Resolve the client IP.

        Proxy headers (CF-Connecting-IP / X-Real-IP / X-Forwarded-For) are SPOOFABLE, so they
        are honoured only when ``trust_proxy=True`` (the deployment is behind a known proxy that
        sets them). Otherwise the socket peer is used. Secure by default.
        """
        if trust_proxy:
            headers = {k.lower(): v for k, v in request.headers.items()}
            for name in ("cf-connecting-ip", "x-real-ip"):
                if headers.get(name):
                    return headers[name].strip()
            xff = headers.get("x-forwarded-for")
            if xff:
                return xff.split(",")[0].strip()
        return request.client_ip

    @classmethod
    def check_ip_allowlist(
        cls, request: WebhookRequest, cidrs: list[str], *, trust_proxy: bool = False
    ) -> None:
        """IP-authenticated providers (e.g. YooKassa). Only honour proxy headers when the
        gateway config opts into ``trust_proxy`` (deployment is behind a trusted proxy)."""
        if not cidrs:
            return
        ip_str = cls.client_ip(request, trust_proxy=trust_proxy)
        if ip_str is None:
            raise WebhookVerificationError("no client IP to check against allowlist")
        ip = ipaddress.ip_address(ip_str)
        for cidr in cidrs:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return
        raise WebhookVerificationError(f"source IP {ip_str} not in allowlist")

    @staticmethod
    def parse_json(body: bytes) -> dict[str, Any]:
        """Parse a JSON webhook body. Returns {} on empty."""
        if not body:
            return {}
        return dict(orjson.loads(body))
