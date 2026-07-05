"""Money as integer minor units (ADR-0002).

Never use float for money. Internally everything is ``amount_minor`` (kopeks / cents /
whole Stars). Convert to :class:`~decimal.Decimal` only at the payment-gateway boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from src.core.enums import Currency


class MoneyError(Exception):
    """Raised on cross-currency arithmetic or invalid amounts."""


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An amount in a single currency, stored as integer minor units.

    ``order=True`` compares by (amount_minor, currency); comparisons across different
    currencies are meaningless — guard with :meth:`_same_currency` in arithmetic.
    """

    amount_minor: int
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int):  # defensive: floats sneak in easily
            raise MoneyError(f"amount_minor must be int, got {type(self.amount_minor).__name__}")

    # --- constructors ------------------------------------------------------
    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(0, currency)

    @classmethod
    def from_major(cls, value: Decimal | int | str, currency: Currency) -> Money:
        """Build from a human amount (e.g. ``Decimal('149.99')`` RUB -> 14999 kopeks)."""
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
        scale = Decimal(10) ** currency.exponent
        minor = int((dec * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return cls(minor, currency)

    # --- conversions -------------------------------------------------------
    def to_major(self) -> Decimal:
        """Convert to a human-facing Decimal (for gateway APIs / display only)."""
        scale = Decimal(10) ** self.currency.exponent
        return (Decimal(self.amount_minor) / scale).quantize(
            Decimal(1).scaleb(-self.currency.exponent), rounding=ROUND_HALF_UP
        )

    # --- arithmetic --------------------------------------------------------
    def _same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise MoneyError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def scale(self, ratio: Decimal | int | str) -> Money:
        """Multiply by a ratio, rounding half-up (used for discounts/percentages)."""
        r = ratio if isinstance(ratio, Decimal) else Decimal(str(ratio))
        minor = int((Decimal(self.amount_minor) * r).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return Money(minor, self.currency)

    def apply_discount(self, percent: int) -> Money:
        """Return the amount after subtracting ``percent`` (0..100), capped to [0, 100]."""
        pct = max(0, min(100, percent))
        return self.scale(Decimal(100 - pct) / Decimal(100))

    @property
    def is_zero(self) -> bool:
        return self.amount_minor == 0

    @property
    def is_positive(self) -> bool:
        return self.amount_minor > 0

    def __str__(self) -> str:
        return f"{self.to_major()} {self.currency.value}"
