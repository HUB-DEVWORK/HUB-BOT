"""PAYMENT_METHOD_ORDER — operator-controlled payment-method ordering (bot + mini-app)."""

from __future__ import annotations

from src.core.payment_order import order_rank


def test_listed_methods_come_first_in_csv_order() -> None:
    ids = ["balance", "stars", "yookassa", "cryptobot"]
    ordered = sorted(ids, key=order_rank("yookassa, stars"))  # stable sort
    assert ordered[:2] == ["yookassa", "stars"]
    assert ordered[2:] == ["balance", "cryptobot"]  # unlisted keep their original order


def test_empty_order_keeps_default_order() -> None:
    ids = ["balance", "stars", "cryptobot"]
    assert sorted(ids, key=order_rank("")) == ids


def test_order_is_case_insensitive_and_trims() -> None:
    rank = order_rank("  YooKassa , STARS ")
    assert rank("yookassa") == 0
    assert rank("stars") == 1
    assert rank("balance") == 2  # unlisted -> last bucket
