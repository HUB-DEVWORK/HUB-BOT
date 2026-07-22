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


def test_legal_doc_actions_registered() -> None:
    # Owner can add «Пользовательское соглашение» / «Политика конфиденциальности» menu buttons.
    assert is_action("terms")
    assert is_action("privacy")
    from src.core.config_registry import REGISTRY

    keys = {p.key for p in REGISTRY}
    assert {"TERMS_TEXT", "PRIVACY_TEXT"} <= keys


def test_reply_dispatch_covers_all_actions() -> None:
    # Every action the constructor can place on the bottom bar must be dispatchable in reply
    # mode, or the button dead-ends. Regression: terms/privacy (added in 1.2.4) were missing.
    from src.bot.handlers.reply_menu import _reply_action_handlers

    covered = set(_reply_action_handlers()) | {"promocode"}  # promocode is FSM-special-cased
    codes = {a.code for a in MENU_ACTIONS}
    missing = codes - covered
    assert not missing, f"reply-mode dispatch missing handlers for: {sorted(missing)}"


def test_reply_dispatch_handler_signatures_match_call() -> None:
    # Regression: `act_cabinet` needs a 4th `state` arg but `_open_action` called the mapped
    # handlers with only 3 → every reply-mode "Личный кабинет" tap crashed. Lock the contract:
    # dict handlers are called with (message, container, db_user); cabinet/promocode are
    # special-cased WITH state. Verify each is callable with the args the dispatcher passes.
    import inspect

    from src.bot.handlers import actions, promo
    from src.bot.handlers.reply_menu import _reply_action_handlers

    def required_positional(fn: object) -> int:
        return sum(
            1
            for p in inspect.signature(fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
        )

    # cabinet + promocode are invoked with 4 positional args (they take FSM state).
    assert required_positional(actions.act_cabinet) == 4
    assert required_positional(promo.ask_code) == 4
    # Every other mapped handler is invoked with exactly 3 (message, container, db_user).
    for code, handler in _reply_action_handlers().items():
        if code == "cabinet":
            continue  # special-cased in _open_action with state
        assert required_positional(handler) == 3, f"{code}: {handler} not callable with 3 args"


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


def test_menu_keyboard_stacks_flat_menu_one_per_row() -> None:
    # A flat menu (every button on row_index=0, the constructor's default) stacks one button
    # per row — what operators expect and it never truncates. The per-row control opts into packing.
    from src.bot.keyboards import menu_keyboard
    from src.core.enums import MenuNodeKind

    nodes = [_node(i, f"Кнопка {i}", MenuNodeKind.ACTION, "buy", row=0, order=i) for i in range(5)]
    markup = menu_keyboard(nodes, None)
    widths = [len(r) for r in markup.inline_keyboard]
    assert widths == [1, 1, 1, 1, 1]  # stacked, one per row
    assert sum(widths) == 5  # every button rendered


def test_menu_keyboard_miniapp_button_requires_https() -> None:
    # A WebApp button with a non-https URL makes Telegram reject the whole message with
    # BUTTON_TYPE_INVALID. A mis-set mini-app URL must degrade, not break the menu send.
    from src.bot.keyboards import menu_keyboard
    from src.core.enums import MenuNodeKind

    node = _node(1, "📱 Приложение", MenuNodeKind.MINIAPP, None)
    # https -> a real web_app button
    ok = menu_keyboard([node], None, miniapp_url="https://app.example")
    assert ok.inline_keyboard[0][0].web_app is not None
    # non-https -> no web_app button (degrades to a harmless non-webapp button)
    bad = menu_keyboard([node], None, miniapp_url="http://app.example")
    assert bad.inline_keyboard[0][0].web_app is None
    none = menu_keyboard([node], None, miniapp_url=None)
    assert none.inline_keyboard[0][0].web_app is None


def test_submenu_back_button_not_duplicated() -> None:
    # A submenu always gets an auto back button (with_back=True); if the operator also placed a
    # BACK node, the bot must not stack two identical back buttons. Back buttons all carry a
    # callback_data ending in ":up", so count those (avoids matching on localized labels).
    from src.bot.keyboards import menu_keyboard
    from src.core.enums import MenuNodeKind
    from src.infrastructure.database.models.menu_node import MenuNode

    child = MenuNode(
        id=2,
        parent_id=1,
        order_index=0,
        row_index=0,
        label="support",
        kind=MenuNodeKind.ACTION,
        payload="support",
        is_active=True,
    )
    manual_back = MenuNode(
        id=3,
        parent_id=1,
        order_index=1,
        row_index=1,
        label="back",
        kind=MenuNodeKind.BACK,
        payload=None,
        is_active=True,
    )

    def backs(markup: object) -> int:
        return sum(
            1
            for row in markup.inline_keyboard  # type: ignore[attr-defined]
            for b in row
            if (b.callback_data or "").endswith(":up")
        )

    assert backs(menu_keyboard([child, manual_back], 1, with_back=True)) == 1  # not doubled
    assert backs(menu_keyboard([child], 1, with_back=True)) == 1  # auto-back still added


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


def test_reply_menu_markup_caps_row_width_at_two() -> None:
    # Regression: the bottom bar honoured row_index verbatim with no width cap, so an
    # operator who put three word-labelled buttons on one row got truncated captions
    # ("Личный каби…"). Rows must wrap at two, preserving order.
    from src.bot.keyboards import reply_menu_markup
    from src.core.enums import MenuNodeKind

    nodes = [
        _node(1, "📱 Открыть", MenuNodeKind.ACTION, "connect", row=0, order=0),
        _node(2, "🛒 Купить", MenuNodeKind.ACTION, "buy", row=0, order=1),
        _node(3, "🎁 Пробный", MenuNodeKind.ACTION, "trial", row=0, order=2),
        _node(4, "👤 Личный кабинет", MenuNodeKind.ACTION, "cabinet", row=1, order=0),
        _node(5, "🔌 Подключить", MenuNodeKind.ACTION, "connect", row=1, order=1),
    ]
    kb = reply_menu_markup(nodes, miniapp_url="")
    assert kb is not None
    widths = [len(r) for r in kb.keyboard]
    assert max(widths) <= 2  # no crowded row -> no truncation
    assert sum(widths) == 5  # every button still rendered
    # row_index-0 overflow wraps: [Открыть, Купить] then [Пробный]; row-1 stays as its pair
    assert [b.text for b in kb.keyboard[0]] == ["📱 Открыть", "🛒 Купить"]


def test_reply_menu_markup_stacks_flat_menu_one_per_row() -> None:
    from src.bot.keyboards import reply_menu_markup
    from src.core.enums import MenuNodeKind

    # All buttons on row_index 0 (constructor default) stack one per row — no truncation,
    # and stacked as operators expect. The per-row control opts into a 2-wide layout.
    nodes = [_node(i, f"Кнопка {i}", MenuNodeKind.ACTION, "buy", row=0, order=i) for i in range(5)]
    kb = reply_menu_markup(nodes, miniapp_url="")
    assert kb is not None
    assert [len(r) for r in kb.keyboard] == [1, 1, 1, 1, 1]
    assert sum(len(r) for r in kb.keyboard) == 5


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
