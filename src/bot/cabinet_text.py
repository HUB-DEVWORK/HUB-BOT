"""Owner-editable text of the «Личный кабинет» screen.

The cabinet profile used to be hardcoded. Now three templates drive it (all editable in the
cabinet, with clickable placeholder chips):

* ``CABINET_TEXT`` — the profile body. Placeholders: ``{имя} {id} {баланс} {друзей}`` and
  ``{подписка}`` (replaced by one of the two sub-blocks below).
* ``CABINET_SUB_ACTIVE`` — the ``{подписка}`` block when the subscription is live. Placeholders:
  ``{срок} {осталось} {устройств} {трафик} {автопродление}``.
* ``CABINET_SUB_INACTIVE`` — the ``{подписка}`` block when there is no active subscription.

A line whose placeholder value is ``None`` is dropped entirely — that's how an off feature
(e.g. traffic display) hides its whole line instead of leaving a dangling label.
"""

from __future__ import annotations

import re

DEFAULT_CABINET_TEXT = (
    "<b>👤 Профиль</b>\n"
    "\n"
    "Привет, {имя}! 👋\n"
    "ID: <code>{id}</code>\n"
    "──────────\n"
    "{подписка}\n"
    "\n"
    "💳 Баланс: <b>{баланс}</b>   ·   🎁 Друзей: <b>{друзей}</b>"
)

DEFAULT_SUB_ACTIVE = (
    "<b>📶 Подписка активна</b>\n"
    "Действует до <b>{срок}</b> · осталось <b>{осталось}</b>\n"
    "📱 Устройств: <b>{устройств}</b>\n"
    "📈 Трафик: <b>{трафик}</b>\n"
    "Автопродление: <b>{автопродление}</b>\n"
    "Ключ-ссылка — в разделе «Моя подписка»."
)

DEFAULT_SUB_INACTIVE = "<b>📶 Подписка не активна</b>\nНе оформлена — нажми «Купить VPN» в меню."

# Placeholder names offered as clickable chips in the cabinet, per template.
MAIN_PLACEHOLDERS = ("имя", "id", "баланс", "друзей", "подписка")
SUB_PLACEHOLDERS = ("срок", "осталось", "устройств", "трафик", "автопродление")

_TOKEN = re.compile(r"\{([а-яёa-z_]+)\}")


def _subst(template: str, values: dict[str, object]) -> str:
    """Replace ``{key}`` tokens from ``values``. A line is dropped whole when any token on it
    maps to ``None`` (an intentionally hidden field); a missing key becomes an empty string."""
    out_lines: list[str] = []
    for line in template.split("\n"):
        tokens = _TOKEN.findall(line)
        if any(values.get(tok, "") is None for tok in tokens):
            continue  # a None-valued placeholder hides its entire line
        out_lines.append(_TOKEN.sub(lambda m: str(values.get(m.group(1), "") or ""), line))
    return "\n".join(out_lines)


def render_cabinet_text(
    *,
    main: str,
    sub_active: str,
    sub_inactive: str,
    is_active: bool,
    values: dict[str, object],
) -> str:
    """Build the full cabinet message: fill the active/inactive sub-block, inline it into the
    main template's ``{подписка}`` slot, then fill the main placeholders."""
    sub_text = _subst(sub_active if is_active else sub_inactive, values)
    return _subst(main.replace("{подписка}", sub_text), values)
