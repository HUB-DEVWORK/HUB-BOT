"""Owner-editable screen buttons: apply_screen_buttons + load_screen_configs.

Covers the safe-rewrite contract: no override is a no-op, overrides reorder/relabel/hide only
the static nav buttons, dynamic buttons (plan lists, autopay toggles) survive untouched, and a
renamed autopay toggle keeps its live state glyph. All static callbacks below are built so that
button_identity() maps them onto the "subscription" screen's registry keys.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.screen_buttons import apply_screen_buttons, load_screen_configs


def _btn(text: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback)


def _kb(buttons: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    # one physical row is enough; apply_screen_buttons flattens before re-grouping.
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def _flat(markup: InlineKeyboardMarkup) -> list[InlineKeyboardButton]:
    return [b for row in markup.inline_keyboard for b in row]


def test_no_config_returns_same_markup_object() -> None:
    markup = _kb(
        [
            _btn("Подключить", "act:connect"),
            _btn("Продлить", "plan"),
            _btn("Меню", "nav:root"),
        ]
    )
    # None, missing "items", and an empty "items" list all pass the markup through untouched.
    assert apply_screen_buttons("subscription", markup, None) is markup
    assert apply_screen_buttons("subscription", markup, {}) is markup
    assert apply_screen_buttons("subscription", markup, {"items": []}) is markup

    unchanged = apply_screen_buttons("subscription", markup, None)
    assert [b.callback_data for b in _flat(unchanged)] == ["act:connect", "plan", "nav:root"]


def test_override_reorders_listed_buttons() -> None:
    markup = _kb(
        [
            _btn("Подключить", "act:connect"),
            _btn("Продлить", "plan"),
            _btn("Меню", "nav:root"),
        ]
    )
    cfg: dict[str, Any] = {
        "items": [
            {"key": "nav:root"},
            {"key": "act:connect"},
            {"key": "plan"},
        ]
    }
    result = apply_screen_buttons("subscription", markup, cfg)
    assert [b.callback_data for b in _flat(result)] == ["nav:root", "act:connect", "plan"]


def test_override_relabels_but_keeps_callback() -> None:
    markup = _kb(
        [
            _btn("Подключить", "act:connect"),
            _btn("Меню", "nav:root"),
        ]
    )
    cfg: dict[str, Any] = {
        "items": [
            {"key": "act:connect", "label": "Connect now"},
            {"key": "nav:root"},
        ]
    }
    first = _flat(apply_screen_buttons("subscription", markup, cfg))[0]
    assert first.text == "Connect now"
    assert first.callback_data == "act:connect"


def test_disabled_button_is_hidden() -> None:
    markup = _kb(
        [
            _btn("Подключить", "act:connect"),
            _btn("Меню", "nav:root"),
        ]
    )
    cfg: dict[str, Any] = {
        "items": [
            {"key": "act:connect"},
            {"key": "nav:root", "enabled": False},
        ]
    }
    result = apply_screen_buttons("subscription", markup, cfg)
    # a disabled button drops out and is NOT re-added by the safety net (it is still referenced).
    assert [b.callback_data for b in _flat(result)] == ["act:connect"]


def test_dynamic_button_not_in_registry_is_preserved_and_appended() -> None:
    # "plan:5" is a live plan button; button_identity strips the trailing id so its identity is
    # "plan", which the config below never references -> it must survive, untouched, at the end.
    markup = _kb(
        [
            _btn("Подключить", "act:connect"),
            _btn("Тариф 5", "plan:5"),
            _btn("Меню", "nav:root"),
        ]
    )
    cfg: dict[str, Any] = {
        "items": [
            {"key": "act:connect"},
            {"key": "nav:root"},
        ]
    }
    result = apply_screen_buttons("subscription", markup, cfg)
    flat = _flat(result)
    assert [b.callback_data for b in flat] == ["act:connect", "nav:root", "plan:5"]

    dynamic = flat[-1]
    assert dynamic.callback_data == "plan:5"
    assert dynamic.text == "Тариф 5"  # appended byte-for-byte, no restyle applied


def test_rename_of_autopay_toggle_preserves_state_glyph() -> None:
    # autopay:toggle is a _DYNAMIC_STATE_KEYS button: its live caption ends in a state glyph the
    # handler appends. Renaming it must keep that trailing indicator so it never freezes.
    markup = _kb([_btn("🔁 Автопродление ✅", "autopay:toggle")])
    cfg: dict[str, Any] = {"items": [{"key": "autopay:toggle", "label": "Автопродление"}]}
    button = _flat(apply_screen_buttons("subscription", markup, cfg))[0]
    assert button.callback_data == "autopay:toggle"
    assert button.text == "Автопродление: ✅"


def test_rename_of_autopay_toggle_preserves_cross_glyph() -> None:
    markup = _kb([_btn("🔁 Автопродление ❌", "autopay:toggle")])
    cfg: dict[str, Any] = {"items": [{"key": "autopay:toggle", "label": "Autopay"}]}
    button = _flat(apply_screen_buttons("subscription", markup, cfg))[0]
    assert button.text == "Autopay: ❌"


def test_load_screen_configs_parses_valid_object() -> None:
    raw = '{"subscription": {"items": [{"key": "nav:root"}]}}'
    assert load_screen_configs(raw) == {"subscription": {"items": [{"key": "nav:root"}]}}


def test_load_screen_configs_swallows_bad_input() -> None:
    assert load_screen_configs("{not json") == {}
    assert load_screen_configs(None) == {}
    assert load_screen_configs("") == {}
    assert load_screen_configs("   ") == {}
    # valid JSON that is not an object (a bare array) collapses to an empty dict, no raise.
    assert load_screen_configs("[1, 2, 3]") == {}
