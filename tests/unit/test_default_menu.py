"""Default menu + action catalogue invariants (src/bot/default_menu.py)."""

from __future__ import annotations

from src.bot.default_menu import DEFAULT_MENU, MENU_ACTIONS, action, is_action


def test_action_codes_unique() -> None:
    codes = [a.code for a in MENU_ACTIONS]
    assert len(codes) == len(set(codes))


def test_default_menu_uses_known_actions() -> None:
    for btn in DEFAULT_MENU:
        assert is_action(btn.action), f"{btn.action} is not a registered menu action"


def test_default_menu_non_empty_and_labelled() -> None:
    assert DEFAULT_MENU
    for btn in DEFAULT_MENU:
        assert btn.label.strip()
        assert btn.action


def test_lookup_helpers() -> None:
    assert is_action("buy")
    assert not is_action("does_not_exist")
    buy = action("buy")
    assert buy is not None and buy.label_ru
    assert action("does_not_exist") is None
