"""Branding: logo / sticker / per-screen banner (photo & sticker commands)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from src.bot.banners import SCREEN_KEYS, banner_config_key
from src.bot.handlers.admin._common import back_kb
from src.bot.screen import show_screen
from src.infrastructure.di import AppContainer

router = Router(name="admin-branding")


@router.callback_query(F.data == "admin:brand")
async def admin_brand(cb: CallbackQuery) -> None:
    text = (
        "🖼 <b>Оформление</b>\n\n"
        "• Лого: <code>/setlogo</code> ответом на фото (убрать — <code>/dellogo</code>).\n"
        "• Стикер: <code>/setsticker</code> на стикер (снять — <code>/delsticker</code>).\n"
        "• Баннер экрана: <code>/setbanner экран</code> ответом на фото "
        "(<code>/setbanner</code> без имени — для всех; убрать — <code>/delbanner</code>).\n"
        f"  Экраны: {', '.join(SCREEN_KEYS)}.\n"
        "• Кнопки/цвета/меню — в веб-админке → «Конструктор меню»."
    )
    await show_screen(cb, text, back_kb())
    await cb.answer()


async def _set_config(container: AppContainer, key: str, value: str) -> None:
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {key: value})
        await uow.commit()


@router.message(Command("setlogo"))
async def set_logo(message: Message, container: AppContainer) -> None:
    """Set the /start logo: reply to a photo with /setlogo (or a photo captioned /setlogo)."""
    source = (
        message.reply_to_message
        if (message.reply_to_message and message.reply_to_message.photo)
        else message
    )
    if not source.photo:
        await message.answer("Пришли /setlogo ответом на фото (или фото с подписью /setlogo).")
        return
    await _set_config(container, "WELCOME_IMAGE", source.photo[-1].file_id)
    await message.answer("✅ Лого обновлено — проверь /start.")


@router.message(Command("dellogo"))
async def del_logo(message: Message, container: AppContainer) -> None:
    await _set_config(container, "WELCOME_IMAGE", "")
    await message.answer("Лого убрано.")


@router.message(Command("setsticker"))
async def set_sticker(message: Message, container: AppContainer) -> None:
    """Set the /start sticker: reply to a sticker with /setsticker."""
    source = (
        message.reply_to_message
        if (message.reply_to_message and message.reply_to_message.sticker)
        else message
    )
    if source.sticker is None:
        await message.answer("Пришли /setsticker ответом на стикер.")
        return
    await _set_config(container, "WELCOME_STICKER", source.sticker.file_id)
    await message.answer("✅ Стикер обновлён — проверь /start.")


@router.message(Command("delsticker"))
async def del_sticker(message: Message, container: AppContainer) -> None:
    await _set_config(container, "WELCOME_STICKER", "")
    await message.answer("Стикер убран.")


def _screen_arg(command: CommandObject) -> str:
    parts = (command.args or "").strip().lower().split()
    return parts[0] if parts else "default"


@router.message(Command("setbanner"))
async def set_banner(message: Message, command: CommandObject, container: AppContainer) -> None:
    """Set a screen banner: reply to a photo with /setbanner <экран> (empty = default)."""
    source = (
        message.reply_to_message
        if (message.reply_to_message and message.reply_to_message.photo)
        else message
    )
    if not source.photo:
        await message.answer(
            "Пришли <code>/setbanner экран</code> ответом на фото (или фото с такой подписью).\n"
            f"Экраны: {', '.join(SCREEN_KEYS)}. Пусто — баннер по умолчанию для всех.",
            parse_mode="HTML",
        )
        return
    key = banner_config_key(_screen_arg(command))
    await _set_config(container, key, source.photo[-1].file_id)
    await message.answer(f"✅ Баннер <code>{key}</code> обновлён.", parse_mode="HTML")


@router.message(Command("delbanner"))
async def del_banner(message: Message, command: CommandObject, container: AppContainer) -> None:
    """Remove a screen banner: /delbanner <экран> (empty = default banner)."""
    key = banner_config_key(_screen_arg(command))
    await _set_config(container, key, "")
    await message.answer(f"Баннер <code>{key}</code> убран.", parse_mode="HTML")
