"""Owner-editable cabinet text: placeholders, {подписка} block, line-hiding."""

from __future__ import annotations

from src.bot.cabinet_text import (
    DEFAULT_CABINET_TEXT,
    DEFAULT_SUB_ACTIVE,
    DEFAULT_SUB_INACTIVE,
    render_cabinet_text,
)

_VALS = {
    "имя": "Иван",
    "id": 123,
    "баланс": "150,00 ₽",
    "друзей": 3,
    "срок": "25.08.2026",
    "осталось": "32 дн. 5 ч.",
    "устройств": 3,
    "трафик": "12.4 / ∞",
    "автопродление": "вкл",
}


def _render(is_active: bool, vals: dict[str, object]) -> str:
    return render_cabinet_text(
        main=DEFAULT_CABINET_TEXT,
        sub_active=DEFAULT_SUB_ACTIVE,
        sub_inactive=DEFAULT_SUB_INACTIVE,
        is_active=is_active,
        values=vals,
    )


def test_active_fills_all_placeholders() -> None:
    out = _render(True, _VALS)
    assert "Привет, Иван!" in out
    assert "<code>123</code>" in out
    assert "150,00 ₽" in out
    assert "Друзей: <b>3</b>" in out
    assert "Подписка активна" in out
    assert "25.08.2026" in out and "32 дн. 5 ч." in out
    assert "Трафик: <b>12.4 / ∞</b>" in out
    assert "Автопродление: <b>вкл</b>" in out
    assert "{" not in out  # every token resolved


def test_inactive_uses_inactive_block() -> None:
    out = _render(False, {**_VALS, "срок": None})
    assert "Подписка не активна" in out
    assert "Купить VPN" in out
    assert "Действует до" not in out  # active-only line absent


def test_none_value_hides_its_line() -> None:
    # traffic display off -> {трафик} is None -> the whole «Трафик» line is dropped.
    out = _render(True, {**_VALS, "трафик": None})
    assert "Трафик" not in out
    assert "Устройств" in out  # neighbouring lines survive


def test_custom_template_and_reorder() -> None:
    out = render_cabinet_text(
        main="Баланс {баланс}\n{подписка}",
        sub_active="осталось {осталось}",
        sub_inactive="нет подписки",
        is_active=True,
        values=_VALS,
    )
    assert out == "Баланс 150,00 ₽\nосталось 32 дн. 5 ч."  # noqa: RUF001


def test_missing_placeholder_is_empty_not_crash() -> None:
    out = render_cabinet_text(
        main="{имя} {неизвестная}",
        sub_active="",
        sub_inactive="",
        is_active=False,
        values={"имя": "Ann"},
    )
    assert out.strip() == "Ann"


def test_apply_custom_emoji() -> None:
    from src.bot.cabinet_text import apply_custom_emoji

    out = apply_custom_emoji("Привет", "5368324170671202286 🔥")
    assert out == '<tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji> Привет'
    # no id or no fallback -> unchanged
    assert apply_custom_emoji("t", "") == "t"
    assert apply_custom_emoji("t", "notdigits 🔥") == "t"
    assert apply_custom_emoji("t", "123") == "t"  # id without fallback char
