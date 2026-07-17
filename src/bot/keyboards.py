"""Keyboard builders: render the admin-built menu tree + built-in flows.

Button colors: Telegram Bot API supports fixed styles (primary/success/danger, aiogram
>= 3.27). The admin picks any HEX in the constructor; we map it to the closest style
(greens -> success, reds -> danger, everything else -> primary, empty -> default).
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

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


def _is_valid_button_url(url: str) -> bool:
    """Telegram inline-button URLs must carry a scheme; a scheme-less one (example.com) is
    rejected with BUTTON_URL_INVALID and takes the whole message down."""
    return url.startswith(("http://", "https://", "tg://"))


def _button(
    node: MenuNode, miniapp_url: str | None, default_color: str | None = None
) -> InlineKeyboardButton:
    kwargs: dict[str, object] = {"text": node.label}
    style = style_for_hex(node.color or default_color)
    if style:
        kwargs["style"] = style
    if node.kind.value == "link" and node.payload and _is_valid_button_url(node.payload):
        kwargs["url"] = node.payload
    elif node.kind.value == "link":
        # A scheme-less URL (e.g. "example.com") makes Telegram reject the WHOLE message with
        # BUTTON_URL_INVALID — bricking the menu render (and the fallback re-sends the same bad
        # markup). Degrade to a harmless bounce (caught by the act:* catch-all) like miniapp does.
        kwargs["callback_data"] = f"act:noop:{node.id}"
    elif node.kind.value == "miniapp" and miniapp_url and miniapp_url.startswith("https://"):
        # A WebApp button REQUIRES an https URL; a non-https one makes Telegram reject the whole
        # message with BUTTON_TYPE_INVALID. Guard it so a mis-set mini-app URL degrades to a
        # harmless dead button instead of breaking the entire menu send.
        from aiogram.types import WebAppInfo

        kwargs["web_app"] = WebAppInfo(url=miniapp_url)
    elif node.kind.value == "back":
        kwargs["callback_data"] = f"nav:{node.parent_id or 0}:up"
    elif node.kind.value == "screen":
        kwargs["callback_data"] = f"nav:{node.id}"
    else:  # action
        kwargs["callback_data"] = f"act:{node.payload or 'noop'}:{node.id}"
    return InlineKeyboardButton(**kwargs)  # type: ignore[arg-type]


# Telegram shrinks buttons to fit a row and truncates labels ("…нет") when a row is crowded.
# Cap how many buttons ever share one physical row.
_MAX_PER_ROW = 3  # hard ceiling even for an explicit row group
# No deliberate layout (every button on the same row_index) -> one button per row (stacked).
# This is what operators expect out of the box and never truncates; the cabinet's per-row
# control assigns row_index to deliberately pack more buttons side by side.
_AUTO_GRID_WIDTH = 1
# Reply-keyboard captions are full words ("Личный кабинет") and truncate sooner than inline
# buttons, so even a deliberate layout never packs more than two per row on the bottom bar.
_REPLY_MAX_PER_ROW = 2


def menu_keyboard(
    nodes: Sequence[MenuNode],
    parent_id: int | None,
    *,
    miniapp_url: str | None = None,
    with_back: bool = False,
    default_color: str | None = None,
) -> InlineKeyboardMarkup:
    siblings = sorted(
        (n for n in nodes if n.parent_id == parent_id and n.is_active),
        key=lambda n: (n.row_index, n.order_index),
    )
    rows: list[list[InlineKeyboardButton]] = []
    # The web constructor doesn't persist row_index yet, so a custom menu arrives with
    # every button on row 0 — laying them all in one physical row truncates the labels.
    # When no deliberate layout is set (a single distinct row_index), auto-wrap into a
    # tidy grid; otherwise honour the explicit rows but still cap each at _MAX_PER_ROW.
    deliberate_layout = len({n.row_index for n in siblings}) > 1
    per_row = _MAX_PER_ROW if deliberate_layout else _AUTO_GRID_WIDTH
    current: int | None = None
    for n in siblings:
        new_group = n.row_index != current  # a new row_index always starts a new row
        if not rows or (deliberate_layout and new_group) or len(rows[-1]) >= per_row:
            rows.append([])
            current = n.row_index
        rows[-1].append(_button(n, miniapp_url, default_color))
    if with_back and parent_id is not None:
        # Go up exactly one level (nav_screen resolves the parent); top-level -> main menu.
        rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=f"nav:{parent_id}:up")])
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


def reply_menu_markup(
    nodes: Sequence[MenuNode],
    *,
    miniapp_url: str | None = None,
    miniapp_label: str = "📱 Приложение",
    extras: Sequence[str] = (),
) -> ReplyKeyboardMarkup | None:
    """Persistent bottom-bar (reply keyboard) from the top-level menu tree.

    Text buttons carry only their label (the reply-menu dispatcher maps it back to the action);
    a mini-app node becomes a native ``web_app`` button that opens the app in one tap. Reply
    keyboards can't hold url/callback buttons, so link nodes fall back to plain text buttons.
    """
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

    has_app = bool(miniapp_url and miniapp_url.startswith("https://"))
    siblings = sorted(
        (n for n in nodes if n.parent_id is None and n.is_active),
        key=lambda n: (n.row_index, n.order_index),
    )
    # No deliberate layout (every button on the same row_index) -> one button per row, so a
    # freshly built menu stacks vertically and never truncates its captions. When the operator
    # sets rows in the cabinet's per-row control, honour that grouping but cap at two per row.
    deliberate_layout = len({n.row_index for n in siblings}) > 1
    per_row = _REPLY_MAX_PER_ROW if deliberate_layout else 1
    rows: list[list[KeyboardButton]] = []
    current: int | None = None
    for n in siblings:
        if n.kind.value == "back":
            continue
        if n.kind.value == "miniapp":
            if not has_app:
                continue
            button = KeyboardButton(text=n.label, web_app=WebAppInfo(url=miniapp_url or ""))
        else:
            button = KeyboardButton(text=n.label)
        new_group = n.row_index != current
        if not rows or (deliberate_layout and new_group) or len(rows[-1]) >= per_row:
            rows.append([])
            current = n.row_index
        rows[-1].append(button)
    for label in extras:  # runtime smart shortcuts (trial/proxy/nodes), one per row
        rows.append([KeyboardButton(text=label)])
    if has_app and not any(n.kind.value == "miniapp" for n in siblings):
        rows.append([KeyboardButton(text=miniapp_label, web_app=WebAppInfo(url=miniapp_url or ""))])
    if not rows:
        return None
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def default_reply_markup(
    miniapp_url: str | None = None, extras: Sequence[str] = ()
) -> ReplyKeyboardMarkup:
    """Bottom-bar for a fresh shop (no custom menu): the seeded buttons + smart shortcuts + app."""
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

    from src.bot.default_menu import DEFAULT_MENU

    rows: list[list[KeyboardButton]] = []
    current: int | None = None
    for b in DEFAULT_MENU:
        if not rows or b.row != current:
            rows.append([])
            current = b.row
        rows[-1].append(KeyboardButton(text=b.label))
    for label in extras:
        rows.append([KeyboardButton(text=label)])
    if miniapp_url and miniapp_url.startswith("https://"):
        rows.append([KeyboardButton(text="📱 Приложение", web_app=WebAppInfo(url=miniapp_url))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def default_menu_markup(default_color: str | None = None) -> InlineKeyboardMarkup:
    """Grid keyboard for the built-in fallback menu — mirrors the seeded DEFAULT_MENU rows,
    so a fresh shop (before the menu is seeded) shows the same tidy layout, not one column."""
    from src.bot.default_menu import DEFAULT_MENU

    rows: list[list[InlineKeyboardButton]] = []
    current: int | None = None
    for b in DEFAULT_MENU:
        style = style_for_hex(b.color or default_color)
        kwargs: dict[str, object] = {"text": b.label, "callback_data": f"act:{b.action}:0"}
        if style:
            kwargs["style"] = style
        button = InlineKeyboardButton(**kwargs)  # type: ignore[arg-type]
        if not rows or b.row != current:
            rows.append([button])
            current = b.row
        else:
            rows[-1].append(button)
    return InlineKeyboardMarkup(inline_keyboard=rows)
