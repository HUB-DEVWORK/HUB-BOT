"""Owner-editable screen captions: default resolution, overrides, placeholder catalogue.

Covers src/bot/screen_text.py - the templating that lets an owner rewrite a screen's caption
while live values keep flowing in through {token} placeholders, with a default fallback that
keeps a mis-typed template harmless.
"""

from __future__ import annotations

from src.bot.screen_text import (
    CABINET_DEFAULT_TEMPLATE,
    cabinet_sub_placeholders,
    caption_for,
    default_text_for,
    placeholders_for,
    render_template,
    sample_for,
)

# A small illustrative context; values are plain strings, as caption_for expects.
_CTX: dict[str, str] = {"имя": "Ann", "id": "42", "баланс": "150,00 ₽"}


def test_default_text_for_known_screen() -> None:
    assert default_text_for("cabinet") == CABINET_DEFAULT_TEMPLATE
    assert default_text_for("connect").startswith("<b>")
    assert "Подключение" in default_text_for("connect")


def test_default_text_for_unknown_screen_is_empty() -> None:
    assert default_text_for("does-not-exist") == ""


def test_caption_for_empty_template_returns_default() -> None:
    default = default_text_for("balance")
    # None, empty and whitespace-only templates all fall through to the default byte-for-byte.
    assert caption_for(None, default, _CTX) == default
    assert caption_for("", default, _CTX) == default
    assert caption_for("   \n  ", default, _CTX) == default


def test_caption_for_owner_override_replaces_default() -> None:
    default = default_text_for("balance")
    out = caption_for("Ваш баланс: {баланс}", default, _CTX)
    assert out == "Ваш баланс: 150,00 ₽"
    assert out != default


def test_caption_for_blank_render_falls_back_to_default() -> None:
    # A non-blank template whose single token resolves to whitespace renders blank -> default.
    default = default_text_for("balance")
    assert caption_for("{баланс}", default, {"баланс": "   "}) == default


def test_render_template_fills_known_leaves_unknown_literal() -> None:
    out = render_template("{имя}: {баланс}, {неизвестно}", {"имя": "Ann", "баланс": "0"})
    assert out == "Ann: 0, {неизвестно}"


def test_render_template_leaves_surrounding_html_untouched() -> None:
    assert render_template("<b>{имя}</b>", {"имя": "Ann"}) == "<b>Ann</b>"


def test_placeholders_for_shape_and_content() -> None:
    chips = placeholders_for("balance")
    assert chips == [
        {"token": "имя", "desc": "Имя пользователя"},
        {"token": "id", "desc": "Telegram ID"},
        {"token": "баланс", "desc": "Баланс"},
    ]


def test_placeholders_for_unknown_screen_is_empty() -> None:
    assert placeholders_for("does-not-exist") == []


def test_cabinet_sub_placeholders_shape() -> None:
    chips = cabinet_sub_placeholders()
    assert [c["token"] for c in chips] == [
        "срок",
        "осталось",
        "устройств",
        "трафик",
        "автопродление",
    ]
    assert all(set(c) == {"token", "desc"} for c in chips)


def test_sample_for_returns_a_defensive_copy() -> None:
    first = sample_for("cabinet")
    assert first["имя"] == "Иван"
    first["имя"] = "mutated"
    # Mutating the returned dict must not leak into the module-level sample.
    assert sample_for("cabinet")["имя"] == "Иван"


def test_sample_for_unknown_screen_is_empty() -> None:
    assert sample_for("does-not-exist") == {}


def test_cabinet_default_template_fully_resolves_with_its_sample() -> None:
    out = caption_for(CABINET_DEFAULT_TEMPLATE, "fallback", sample_for("cabinet"))
    assert "Привет, Ann!" not in out  # sample name is Иван, not the _CTX value
    assert "Привет, Иван!" in out
    assert "<code>123456789</code>" in out
    assert "{" not in out  # every token in the stock template has a sample value
