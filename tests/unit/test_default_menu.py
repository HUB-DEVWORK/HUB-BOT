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


def test_menu_keyboard_auto_wraps_flat_menu() -> None:
    # Regression: the web constructor saves every button on row_index=0. Rendering all
    # of them in one physical row truncates the labels ("…нет"). A flat menu must be
    # auto-laid into a tidy grid instead of one crowded row.
    from src.bot.keyboards import menu_keyboard
    from src.core.enums import MenuNodeKind

    nodes = [_node(i, f"Кнопка {i}", MenuNodeKind.ACTION, "buy", row=0, order=i) for i in range(5)]
    markup = menu_keyboard(nodes, None)
    widths = [len(r) for r in markup.inline_keyboard]
    assert max(widths) <= 2  # no crowded row -> no truncation
    assert sum(widths) == 5  # every button still rendered


def test_menu_keyboard_respects_explicit_rows_but_caps_width() -> None:
    from src.bot.keyboards import menu_keyboard
    from src.core.enums import MenuNodeKind

    # Explicit 2-per-row layout (like the manual prod fix) is honoured exactly.
    nodes = [
        _node(1, "A", MenuNodeKind.ACTION, "buy", row=0, order=0),
        _node(2, "B", MenuNodeKind.ACTION, "balance", row=1, order=0),
        _node(3, "C", MenuNodeKind.ACTION, "history", row=1, order=1),
        _node(4, "D", MenuNodeKind.ACTION, "connect", row=2, order=0),
        _node(5, "E", MenuNodeKind.ACTION, "support", row=2, order=1),
    ]
    markup = menu_keyboard(nodes, None)
    assert [len(r) for r in markup.inline_keyboard] == [1, 2, 2]


def _node(id_: int, label: str, kind, payload, row: int = 0, order: int = 0):  # type: ignore[no-untyped-def]
    from src.infrastructure.database.models.menu_node import MenuNode

    return MenuNode(
        id=id_,
        parent_id=None,
        order_index=order,
        row_index=row,
        label=label,
        kind=kind,
        payload=payload,
        is_active=True,
    )


def test_reply_menu_markup_bottom_bar_with_web_app_button() -> None:
    from src.bot.keyboards import reply_menu_markup
    from src.core.enums import MenuNodeKind

    nodes = [
        _node(1, "🛒 Купить", MenuNodeKind.ACTION, "buy", row=0, order=0),
        _node(2, "👤 Кабинет", MenuNodeKind.ACTION, "cabinet", row=0, order=1),
        _node(3, "📱 Приложение", MenuNodeKind.MINIAPP, None, row=1),
    ]
    kb = reply_menu_markup(nodes, miniapp_url="https://app.example")
    assert kb is not None and kb.is_persistent and kb.resize_keyboard
    assert [len(r) for r in kb.keyboard] == [2, 1]  # row0: two actions; row1: app
    assert kb.keyboard[0][0].web_app is None  # action buttons are plain text (dispatched by label)
    app_btn = kb.keyboard[1][0]
    assert app_btn.web_app is not None and app_btn.web_app.url == "https://app.example"


def test_reply_menu_markup_auto_appends_app_when_tree_has_none() -> None:
    from src.bot.keyboards import reply_menu_markup
    from src.core.enums import MenuNodeKind

    kb = reply_menu_markup(
        [_node(1, "🛒 Купить", MenuNodeKind.ACTION, "buy")], miniapp_url="https://app.example"
    )
    assert kb is not None and kb.keyboard[-1][0].web_app is not None


def test_reply_menu_markup_omits_app_without_https() -> None:
    from src.bot.keyboards import reply_menu_markup
    from src.core.enums import MenuNodeKind

    kb = reply_menu_markup([_node(1, "🛒 Купить", MenuNodeKind.ACTION, "buy")], miniapp_url="")
    assert kb is not None and all(b.web_app is None for row in kb.keyboard for b in row)


def test_reply_menu_markup_none_when_no_renderable_button() -> None:
    # Tree with only a non-https mini-app renders nothing -> None, so the caller falls back to
    # DEFAULT_MENU and the dispatcher's DEFAULT_MENU branch stays in sync (no dead-end).
    from src.bot.keyboards import reply_menu_markup
    from src.core.enums import MenuNodeKind

    node = _node(1, "📱 App", MenuNodeKind.MINIAPP, None)
    assert reply_menu_markup([node], miniapp_url="") is None


def test_reply_menu_markup_includes_smart_extras() -> None:
    from src.bot.keyboards import reply_menu_markup
    from src.core.enums import MenuNodeKind

    kb = reply_menu_markup(
        [_node(1, "🛒 Купить", MenuNodeKind.ACTION, "buy")],
        miniapp_url="",
        extras=["🎁 Пробный"],
    )
    assert kb is not None
    assert "🎁 Пробный" in [b.text for row in kb.keyboard for b in row]


def test_smart_extras_labels_are_known_actions() -> None:
    # Every smart-extra maps to a real action, or the reply dispatcher would dead-end on it.
    from src.bot.default_menu import SMART_EXTRAS

    for _label, code in SMART_EXTRAS:
        assert is_action(code), f"smart extra {code} is not a registered action"


def test_default_reply_markup_app_button_only_with_url() -> None:
    from src.bot.keyboards import default_reply_markup

    with_app = default_reply_markup("https://app.example")
    assert any(b.web_app for row in with_app.keyboard for b in row)
    without = default_reply_markup(None)
    assert without.is_persistent
    assert all(b.web_app is None for row in without.keyboard for b in row)
