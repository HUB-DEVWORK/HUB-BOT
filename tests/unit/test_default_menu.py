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


def test_menu_keyboard_groups_buttons_by_row_index() -> None:
    from src.bot.keyboards import menu_keyboard
    from src.core.enums import MenuNodeKind
    from src.infrastructure.database.models.menu_node import MenuNode

    nodes = [
        MenuNode(
            id=1,
            parent_id=None,
            order_index=0,
            row_index=0,
            label="A",
            kind=MenuNodeKind.ACTION,
            payload="buy",
            is_active=True,
        ),
        MenuNode(
            id=2,
            parent_id=None,
            order_index=0,
            row_index=1,
            label="B",
            kind=MenuNodeKind.ACTION,
            payload="balance",
            is_active=True,
        ),
        MenuNode(
            id=3,
            parent_id=None,
            order_index=1,
            row_index=1,
            label="C",
            kind=MenuNodeKind.ACTION,
            payload="history",
            is_active=True,
        ),
    ]
    markup = menu_keyboard(nodes, None)
    assert [len(r) for r in markup.inline_keyboard] == [1, 2]  # row0: [A]; row1: [B, C]
    assert [b.text for b in markup.inline_keyboard[1]] == ["B", "C"]
