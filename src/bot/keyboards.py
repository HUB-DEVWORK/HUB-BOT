"""Keyboard builders: render the admin-built menu tree + built-in flows.

Button colors: Telegram Bot API supports fixed styles (primary/success/danger, aiogram
>= 3.27). The admin picks any HEX in the constructor; we map it to the closest style
(greens -> success, reds -> danger, everything else -> primary, empty -> default).
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.infrastructure.database.models.menu_node import MenuNode


def style_for_hex(color: str | None) -> str | None:
    if not color or not color.startswith("#"):
        return None
    hex_part = color.lstrip("#")
    if len(hex_part) == 3:
        hex_part = "".join(c * 2 for c in hex_part)
    try:
        r, g, b = (int(hex_part[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    if g > r and g > b:
        return "success"
    if r > g and r > b:
        return "danger"
    return "primary"


def _button(
    node: MenuNode, miniapp_url: str | None, default_color: str | None = None
) -> InlineKeyboardButton:
    kwargs: dict[str, object] = {"text": node.label}
    style = style_for_hex(node.color or default_color)
    if style:
        kwargs["style"] = style
    if node.kind.value == "link" and node.payload:
        kwargs["url"] = node.payload
    elif node.kind.value == "miniapp" and miniapp_url:
        from aiogram.types import WebAppInfo

        kwargs["web_app"] = WebAppInfo(url=miniapp_url)
    elif node.kind.value == "back":
        kwargs["callback_data"] = f"nav:{node.parent_id or 0}:up"
    elif node.kind.value == "screen":
        kwargs["callback_data"] = f"nav:{node.id}"
    else:  # action
        kwargs["callback_data"] = f"act:{node.payload or 'noop'}:{node.id}"
    return InlineKeyboardButton(**kwargs)  # type: ignore[arg-type]


def menu_keyboard(
    nodes: Sequence[MenuNode],
    parent_id: int | None,
    *,
    miniapp_url: str | None = None,
    with_back: bool = False,
    default_color: str | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [_button(n, miniapp_url, default_color)]
        for n in sorted(
            (n for n in nodes if n.parent_id == parent_id and n.is_active),
            key=lambda n: n.order_index,
        )
    ]
    if with_back and parent_id is not None:
        rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="nav:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def simple_keyboard(
    buttons: list[tuple[str, str]], columns: int = 1, *, default_color: str | None = None
) -> InlineKeyboardMarkup:
    """[(text, callback_data)] -> markup. ``default_color`` styles every button."""
    style = style_for_hex(default_color)
    extra: dict[str, object] = {"style": style} if style else {}
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), columns):
        rows.append(
            [
                InlineKeyboardButton(text=text, callback_data=cb, **extra)  # type: ignore[arg-type]
                for text, cb in buttons[i : i + columns]
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def url_keyboard(rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, url=u)] for t, u in rows]
    )


def webapp_button(text: str, url: str) -> InlineKeyboardButton:
    """A button that opens the Telegram Mini-App (requires an https URL)."""
    from aiogram.types import WebAppInfo

    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
