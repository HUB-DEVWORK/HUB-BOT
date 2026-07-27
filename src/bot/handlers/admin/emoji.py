"""Admin tool: read a premium (custom) emoji's id right from the chat.

A custom emoji only carries its numeric ``custom_emoji_id`` inside Telegram
``message.entities`` — the web cabinet never sees it, so the owner used to forward
the emoji to some third-party «id bot». This screen removes that dependency: tap the
button, send any premium emoji, and the bot answers with the id *plus* the ready
``[ce:<id>:<glyph>]`` token (see :mod:`src.bot.custom_emoji`) to paste into any screen,
banner or broadcast.
"""

from __future__ import annotations

from html import escape as hesc

from aiogram import F, Router
from aiogram.enums import MessageEntityType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, MessageEntity

from src.bot.handlers.admin._common import back_kb

router = Router(name="admin-emoji")

# A real, free-to-use custom emoji id, shown live in the prompt so the admin sees exactly
# what «premium emoji» means (same one used as the example in src/bot/custom_emoji.py).
_EXAMPLE_ID = "5368324170671202286"
_EXAMPLE_GLYPH = "🔥"


def _utf16_len(text: str) -> int:
    """Length of ``text`` in UTF-16 code units — the unit Telegram entity offsets use."""
    return len(text.encode("utf-16-le")) // 2


def _slice_utf16(text: str, offset: int, length: int) -> str:
    """Slice ``text`` by a Telegram entity's (offset, length), both in UTF-16 code units."""
    raw = text.encode("utf-16-le")
    return raw[offset * 2 : (offset + length) * 2].decode("utf-16-le", "ignore")


def _collect(message: Message) -> list[tuple[str, str]]:
    """(custom_emoji_id, fallback_glyph) for every custom emoji in the message, deduped.

    Reads both text and caption entities so a photo/animation carrying the emoji works too.
    The fallback glyph is the character the client actually rendered under the custom emoji,
    pulled straight from the source text so the token stays meaningful for non-Premium clients.
    """
    body = message.text or message.caption or ""
    entities = (message.entities or []) + (message.caption_entities or [])
    seen: dict[str, str] = {}
    for ent in entities:
        if ent.type != MessageEntityType.CUSTOM_EMOJI or not ent.custom_emoji_id:
            continue
        glyph = _slice_utf16(body, ent.offset, ent.length).strip() or "🙂"
        seen.setdefault(ent.custom_emoji_id, glyph)
    return list(seen.items())


class EmojiForm(StatesGroup):
    waiting = State()


_PROMPT = (
    "😀 Определение ID премиального эмодзи\n\n"
    "Пришли сюда любое кастомное (премиальное) эмодзи — верну его ID и готовый "
    "токен для вставки в тексты бота.\n\n"
    "Например, такое: "  # a live custom emoji is appended right after, via entities
)


@router.callback_query(F.data == "admin:emoji")
async def emoji_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EmojiForm.waiting)
    text = _PROMPT + _EXAMPLE_GLYPH
    offset = _utf16_len(_PROMPT)
    entity = MessageEntity(
        type=MessageEntityType.CUSTOM_EMOJI,
        offset=offset,
        length=_utf16_len(_EXAMPLE_GLYPH),
        custom_emoji_id=_EXAMPLE_ID,
    )
    # Sent raw (parse_mode=None) so the live example emoji renders from `entities`, not HTML.
    if isinstance(cb.message, Message):
        await cb.message.answer(text, entities=[entity], reply_markup=back_kb(), parse_mode=None)
    await cb.answer()


@router.message(EmojiForm.waiting)
async def emoji_read(message: Message, state: FSMContext) -> None:
    found = _collect(message)
    if not found:
        await message.answer(
            "❌ Не вижу кастомных эмодзи в сообщении.\n\n"
            "Пришли именно премиальное эмодзи (не обычный смайл) — по одному или пачкой.",
            reply_markup=back_kb(),
        )
        return

    await state.clear()
    blocks: list[str] = []
    for emoji_id, glyph in found:
        token = f"[ce:{emoji_id}:{glyph}]"
        preview = f'<tg-emoji emoji-id="{emoji_id}">{hesc(glyph)}</tg-emoji>'
        blocks.append(
            f"ID: <code>{emoji_id}</code>\nТокен: <code>{hesc(token)}</code>\nПревью: {preview}"
        )
    head = "✅ Нашёл кастомное эмодзи:" if len(found) == 1 else f"✅ Нашёл {len(found)} эмодзи:"
    text = (
        f"{head}\n\n"
        + "\n\n".join(blocks)
        + "\n\nВставляй токен в любой текст бота (экран, баннер, рассылка).\n"
        "⚠️ Отрисуется только если у бота-владельца есть Telegram Premium."
    )
    # Direct send with parse_mode=HTML: the <code> token stays literal (copyable), while the
    # <tg-emoji> preview renders — bypassing show_screen so apply_custom_emoji can't expand
    # the copyable token itself.
    await message.answer(text, reply_markup=back_kb(), parse_mode="HTML")
