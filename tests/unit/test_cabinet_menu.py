"""Owner-configurable «Личный кабинет» buttons (CABINET_BUTTONS)."""

from __future__ import annotations

from src.bot.cabinet_menu import (
    CABINET_BUTTONS,
    cabinet_buttons,
    parse_cabinet_buttons,
)

_ALL = {"BALANCE_ENABLED": True, "REFERRAL_ENABLED": True}


def test_parse_orders_and_drops_unknown() -> None:
    assert parse_cabinet_buttons("history,balance") == ["history", "balance"]  # owner order kept
    assert parse_cabinet_buttons("balance, bogus , support") == ["balance", "support"]
    assert parse_cabinet_buttons("BALANCE") == ["balance"]  # case-insensitive
    assert parse_cabinet_buttons("balance,balance") == ["balance"]  # de-duped


def test_parse_empty_falls_back_to_all() -> None:
    keys = [b.key for b in CABINET_BUTTONS]
    assert parse_cabinet_buttons("") == keys
    assert parse_cabinet_buttons(None) == keys
    assert parse_cabinet_buttons("nonsense,also-bad") == keys


def test_cabinet_buttons_render_in_owner_order() -> None:
    out = cabinet_buttons("support,subscription", flags=_ALL)
    assert [label for label, _cb in out] == ["🆘 Поддержка", "🔑 Моя подписка"]
    assert out[1][1] == "act:subscription:0"


def test_disabled_feature_button_is_skipped_even_if_listed() -> None:
    # Owner lists balance + referral, but both features are OFF -> neither renders.
    out = cabinet_buttons(
        "subscription,balance,referral,history",
        flags={"BALANCE_ENABLED": False, "REFERRAL_ENABLED": False},
    )
    keys = [cb for _label, cb in out]
    assert "act:balance:0" not in keys and "act:referral:0" not in keys
    assert "act:subscription:0" in keys and "act:history:0" in keys


def test_removing_a_button_hides_it() -> None:
    out = cabinet_buttons(
        "subscription,balance", flags=_ALL
    )  # history/referral/promo/support dropped
    assert [cb for _l, cb in out] == ["act:subscription:0", "act:balance:0"]


def test_registered_config_key() -> None:
    from src.core.config_registry import REGISTRY

    row = next((p for p in REGISTRY if p.key == "CABINET_BUTTONS"), None)
    assert row is not None and row.default
