"""Pricing / purchase request DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.enums import Currency, PurchaseType
from src.core.money import Money


@dataclass(frozen=True, slots=True)
class PurchaseRequest:
    """A user's intent to buy/renew/change a subscription."""

    user_id: int
    plan_id: int
    duration_days: int
    currency: Currency
    internal_squads: tuple[str, ...] = ()
    external_squad: str | None = None
    purchase_type: PurchaseType = PurchaseType.NEW
    promocode: str | None = None
    subscription_id: int | None = None  # for RENEW / CHANGE


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """The computed price for a :class:`PurchaseRequest`."""

    base: Money
    discount_pct: int
    final: Money
    components: dict[str, int] = field(default_factory=dict)  # component -> minor units

    @property
    def is_free(self) -> bool:
        """A 100%-discount / zero price routes through the free path (skips the gateway)."""
        return self.final.is_zero
