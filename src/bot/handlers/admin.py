"""Admin-only bot commands (in-bot administration). Guarded by ``is_admin`` from the middleware.

`is_admin` is set by ContextMiddleware from ADMIN_IDS / APP__OWNER_IDS / user role.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.infrastructure.di import AppContainer

router = Router(name="admin")


@router.message(Command("setlogo"))
async def set_logo(message: Message, container: AppContainer, is_admin: bool) -> None:
    """Set the /start logo: reply to a photo with /setlogo (or send a photo captioned /setlogo)."""
    if not is_admin:
        return
    source = (
        message.reply_to_message
        if (message.reply_to_message and message.reply_to_message.photo)
        else message
    )
    if not source.photo:
        await message.answer(
            "Пришли /setlogo ответом на фото (или отправь фото с подписью /setlogo)."
        )
        return
    file_id = source.photo[-1].file_id  # largest rendition
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {"WELCOME_IMAGE": file_id})
        await uow.commit()
    await message.answer("✅ Лого обновлено — проверь /start.")


@router.message(Command("dellogo"))
async def del_logo(message: Message, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        return
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {"WELCOME_IMAGE": ""})
        await uow.commit()
    await message.answer("Лого убрано.")
