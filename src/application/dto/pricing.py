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
    # Constructor mode (SALES_MODE=constructor): the price comes from these rows instead
    # of plan_durations/plan_prices; plan_id points at the hidden service plan.
    constructor_period_id: int | None = None
    traffic_pack_id: int | None = None
    # Provisioning overrides (constructor packs); None -> take the value from the plan.
    traffic_limit_bytes: int | None = None
    device_limit: int | None = None


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """The computed price for a :class:`PurchaseRequest`."""

    base: Money
    discount_pct: int
    final: Money
    components: dict[str, int] = field(default_factory=dict)  # component -> minor units
    sale_campaign_id: int | None = None  # limited-quota sale applied (consumed at fulfilment)

    @property
    def is_free(self) -> bool:
        """A 100%-discount / zero price routes through the free path (skips the gateway)."""
        return self.final.is_zero
