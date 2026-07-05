"""Money value object (ADR-0002)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.enums import Currency
from src.core.money import Money, MoneyError


def test_from_major_rounds_half_up() -> None:
    assert Money.from_major("149.99", Currency.RUB).amount_minor == 14999
    assert Money.from_major(Decimal("1.005"), Currency.USD).amount_minor == 101  # half-up


def test_stars_have_no_minor_units() -> None:
    assert Currency.XTR.exponent == 0
    assert Money.from_major(100, Currency.XTR).amount_minor == 100


def test_to_major_roundtrip() -> None:
    assert Money(14999, Currency.RUB).to_major() == Decimal("149.99")


def test_apply_discount_is_capped_and_half_up() -> None:
    price = Money(10000, Currency.RUB)
    assert price.apply_discount(10).amount_minor == 9000
    assert price.apply_discount(100).amount_minor == 0
    assert price.apply_discount(150).amount_minor == 0  # capped at 100
    assert price.apply_discount(-5).amount_minor == 10000  # capped at 0


def test_addition_requires_same_currency() -> None:
    assert (Money(100, Currency.RUB) + Money(50, Currency.RUB)).amount_minor == 150
    with pytest.raises(MoneyError):
        _ = Money(100, Currency.RUB) + Money(50, Currency.USD)


def test_float_amount_is_rejected() -> None:
    with pytest.raises(MoneyError):
        Money(1.5, Currency.RUB)  # type: ignore[arg-type]
