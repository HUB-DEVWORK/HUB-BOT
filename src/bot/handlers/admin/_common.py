"""Shared helpers for the in-bot admin panel (all sub-modules import from here)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, TelegramObject

MENU_CB = "admin:menu"

# Callbacks that legitimately CONSUME the pending form (they read state.get_data before
# clearing it themselves). ClearStaleForm must not wipe the form out from under them —
# doing so silently emptied the broadcast text and made «Рассылка» unusable from the bot.
_FORM_CONSUMING_CB = frozenset({"admin:bc:go"})


class IsAdmin(Filter):
    """Router-level gate: the whole admin sub-tree is owner/admin only.

    Non-admins fall through to the next router (normal user behaviour) instead of
    hitting a per-handler ``if not is_admin`` guard we could forget to add.
    """

    async def __call__(self, _: Any, is_admin: bool = False) -> bool:
        return is_admin


class ClearStaleForm(BaseMiddleware):
    """Drop any pending FSM form before handling an admin *button* press.

    Tapping any inline button means the admin left the previous input flow — clear
    the form so a later stray text (e.g. typing a user id to search) can't land in
    ``balance_apply`` and be booked as a money change. Wired only on
    ``callback_query`` as inner middleware, so it fires just for admins whose callback
    already passed :class:`IsAdmin`. Text is handled by message handlers, and every
    callback either sets a fresh form afterwards or is a neutral screen — so an
    unconditional clear is safe. RedisStorage persists state across restarts, which is
    exactly why this is needed.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        consumes = isinstance(event, CallbackQuery) and (event.data or "") in _FORM_CONSUMING_CB
        if state is not None and not consumes:
            await state.clear()
        return await handler(event, data)


def rub(minor: int) -> str:
    """Minor units -> '1 234 ₽' with thin-space grouping."""
    return f"{minor / 100:,.0f} ₽".replace(",", " ")


def back_kb(to: str = MENU_CB, text: str = "‹ Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=to)]]
    )


def rows_kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """[[(text, callback_data), ...], ...] -> markup (explicit rows for mixed layouts)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=c) for t, c in row] for row in rows
        ]
    )


def parse_ints(text: str, count: int) -> list[int] | None:
    """Parse exactly ``count`` whitespace-separated integers, else None.

    Trailing extras are rejected so '30 50 oops' can't silently mean '30 50'.
    """
    parts = (text or "").split()
    if len(parts) != count:
        return None
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            return None
    return out
