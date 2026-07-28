"""Owner-configurable «Личный кабинет» buttons (CABINET_BUTTONS)."""

from __future__ import annotations

from src.bot.cabinet_menu import (
    CABINET_BUTTONS,
    cabinet_buttons,
    parse_cabinet_buttons,
)

_ALL = {"BALANCE_ENABLED": True, "REFERRAL_ENABLED": True}


# parse_config injects the built-in «app» + «back» buttons enabled-by-default so an upgraded
# install never loses its mini-app / exit buttons; the legacy helpers surface them too.
_TAIL = ["app", "back"]


def test_parse_orders_and_drops_unknown() -> None:
    assert parse_cabinet_buttons("history,balance") == ["history", "balance", *_TAIL]  # order kept
    assert parse_cabinet_buttons("balance, bogus , support") == ["balance", "support", *_TAIL]
    assert parse_cabinet_buttons("BALANCE") == ["balance", *_TAIL]  # case-insensitive
    assert parse_cabinet_buttons("balance,balance") == ["balance", *_TAIL]  # de-duped


def test_parse_empty_falls_back_to_all() -> None:
    keys = [b.key for b in CABINET_BUTTONS]
    assert parse_cabinet_buttons("") == keys
    assert parse_cabinet_buttons(None) == keys
    assert parse_cabinet_buttons("nonsense,also-bad") == keys


def test_cabinet_buttons_render_in_owner_order() -> None:
    # cabinet_buttons now yields (label, callback, extra) triples — extra carries icon/style.
    # The owner's listed buttons come first, in order, then the injected app + back tail.
    out = cabinet_buttons("support,subscription", flags=_ALL)
    assert [label for label, _cb, _extra in out][:2] == ["🆘 Поддержка", "🔑 Моя подписка"]
    assert out[1][1] == "act:subscription:0"


def test_disabled_feature_button_is_skipped_even_if_listed() -> None:
    # Owner lists balance + referral, but both features are OFF -> neither renders.
    out = cabinet_buttons(
        "subscription,balance,referral,history",
        flags={"BALANCE_ENABLED": False, "REFERRAL_ENABLED": False},
    )
    keys = [cb for _label, cb, _extra in out]
    assert "act:balance:0" not in keys and "act:referral:0" not in keys
    assert "act:subscription:0" in keys and "act:history:0" in keys


def test_removing_a_button_hides_it() -> None:
    out = cabinet_buttons(
        "subscription,balance", flags=_ALL
    )  # history/referral/promo/support dropped (app + back always injected)
    assert [cb for _l, cb, _extra in out] == ["act:subscription:0", "act:balance:0", "", "nav:root"]


def test_registered_config_key() -> None:
    from src.core.config_registry import REGISTRY

    row = next((p for p in REGISTRY if p.key == "CABINET_BUTTONS"), None)
    assert row is not None and row.default


def test_parse_custom_buttons_validates() -> None:
    from src.bot.cabinet_menu import parse_custom_buttons

    ok = parse_custom_buttons(
        '[{"label":"Канал","url":"https://t.me/x"},{"label":"Сайт","url":"http://a.b"}]'
    )
    assert ok == [
        {"label": "Канал", "url": "https://t.me/x"},
        {"label": "Сайт", "url": "http://a.b"},
    ]
    # bad url dropped, empty label dropped, non-json -> []
    assert parse_custom_buttons('[{"label":"X","url":"ftp://no"}]') == []
    assert parse_custom_buttons('[{"label":"","url":"https://a"}]') == []
    assert parse_custom_buttons("not json") == []
    assert parse_custom_buttons("") == []
    assert parse_custom_buttons('[{"label":"tg","url":"tg://resolve?domain=x"}]')[0][
        "url"
    ].startswith("tg://")


def test_custom_buttons_normalize_scheme() -> None:
    from src.bot.cabinet_menu import normalize_button_url, parse_custom_buttons

    # bare t.me/… gets https:// (else Telegram BUTTON_URL_INVALID bricks the whole screen)
    assert normalize_button_url("t.me/chan") == "https://t.me/chan"
    assert normalize_button_url("https://a.b") == "https://a.b"
    assert normalize_button_url("tg://resolve?domain=x") == "tg://resolve?domain=x"
    assert normalize_button_url("example.com") is None  # scheme-less non-t.me -> rejected
    assert normalize_button_url("javascript:alert(1)") is None
    got = parse_custom_buttons('[{"label":"Канал","url":"t.me/chan"}]')
    assert got == [{"label": "Канал", "url": "https://t.me/chan"}]
