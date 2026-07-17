"""Update screen: check GitHub for a newer version and apply it with one tap.

Owner/admin only (the whole admin sub-tree is gated by IsAdmin). «Обновить» drops a marker
that the updater sidecar (docker/compose updater service) picks up and runs scripts/update.sh —
which backs up the DB, git-pulls, rebuilds and restarts. If the sidecar isn't enabled the
marker path won't exist and we tell the operator to run ./scripts/update.sh by hand.
"""

from __future__ import annotations

from html import escape as hesc

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.handlers.admin._common import back_kb
from src.bot.screen import show_screen
from src.infrastructure.di import AppContainer
from src.infrastructure.services.updater import check_for_update, request_update

router = Router(name="admin-update")


async def _read_cfg(container: AppContainer) -> tuple[str, str, bool]:
    async with container.uow() as uow:
        repo = str(await container.bot_config.value(uow, "UPDATE_REPO") or "")
        branch = str(await container.bot_config.value(uow, "UPDATE_BRANCH") or "main")
        auto = bool(await container.bot_config.value(uow, "AUTO_UPDATE_ENABLED"))
    return repo, branch, auto


@router.callback_query(F.data == "admin:update")
async def screen(cb: CallbackQuery, container: AppContainer) -> None:
    repo, branch, auto = await _read_cfg(container)
    info = await check_for_update(repo, branch, container.settings.app.build_sha)
    cur = info.current or "неизвестна (образ без build-arg)"
    if not info.latest:
        text = (
            "🔄 <b>Обновление</b>\n\n"
            f"Текущая версия: <code>{hesc(cur)}</code>\n"
            "Не удалось проверить GitHub — попробуйте позже."
        )
        await show_screen(cb, text, back_kb())
        return
    if not info.available:
        text = (
            "🔄 <b>Обновление</b>\n\n"
            f"Версия: <code>{hesc(info.latest)}</code>\n✅ У вас последняя версия."
        )
        await show_screen(cb, text, back_kb())
        return
    auto_line = (
        "🟢 Автоустановка включена — обновление поставится само в течение 6 ч.\n\n"
        if auto
        else "⚪️ Автоустановка выключена (включается в кабинете: Настройки → Безопасность).\n\n"
    )
    text = (
        "🔄 <b>Доступно обновление</b>\n\n"
        f"Текущая: <code>{hesc(cur)}</code>\nНовая: <code>{hesc(info.latest)}</code>\n"
        f"{hesc(info.message)}\n\n"
        f"{auto_line}"
        "«Обновить» снимет бэкап БД, скачает новую версию, пересоберётся и перезапустится "
        "(пара минут)."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить сейчас", callback_data="upd:apply")],
            [InlineKeyboardButton(text="🔗 Что нового", url=info.url)],
            [InlineKeyboardButton(text="‹ Назад", callback_data="admin:menu")],
        ]
    )
    await show_screen(cb, text, kb)


@router.callback_query(F.data == "upd:apply")
async def apply(cb: CallbackQuery, container: AppContainer) -> None:
    if request_update():
        text = (
            "🚀 <b>Обновление запущено</b>\n\n"
            "Бот снимет бэкап, обновится и перезапустится (пара минут). Об итоге придёт "
            "отдельное сообщение — ✅ если успешно или ⚠️ если что-то не так (данные в бэкапе целы)."
        )
    else:
        text = (
            "⚠️ <b>Авто-обновление недоступно</b>\n\n"
            "Модуль обновлений (updater) не подключён на этом сервере. Обновите вручную:\n"
            "<code>cd &lt;папка бота&gt; &amp;&amp; ./scripts/update.sh</code>"
        )
    await show_screen(cb, text, back_kb())
    await cb.answer()
