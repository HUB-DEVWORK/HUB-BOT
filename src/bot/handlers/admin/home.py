"""Admin panel home: /admin entry, main menu, /resetmenu."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.bot.screen import show_screen
from src.infrastructure.di import AppContainer

router = Router(name="admin-home")


def _menu(admin_url: str, extras: list[tuple[str, str]] | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="📈 Аналитика", callback_data="admin:analytics"),
        ],
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
            InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:bc"),
        ],
        [
            InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin:promo"),
            InlineKeyboardButton(text="🎁 Gift-коды", callback_data="admin:gift"),
        ],
        [
            InlineKeyboardButton(text="🏷 Акции", callback_data="admin:sales"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin:settings"),
        ],
        [
            InlineKeyboardButton(text="🖼 Оформление", callback_data="admin:brand"),
            InlineKeyboardButton(text="🔄 Обновление", callback_data="admin:update"),
        ],
    ]
    # Module-contributed admin buttons (admin_menu hook), 2 per row, after the
    # static grid and before the web-admin / back rows.
    extras = extras or []
    for i in range(0, len(extras), 2):
        rows.append([InlineKeyboardButton(text=t, callback_data=cb) for t, cb in extras[i : i + 2]])
    if admin_url.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🌐 Веб-админка", url=admin_url)])
    rows.append([InlineKeyboardButton(text="‹ В меню бота", callback_data="nav:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _admin_url(container: AppContainer) -> str:
    async with container.uow() as uow:
        return str(await container.bot_config.value(uow, "ADMIN_PANEL_URL") or "")


async def _extra_buttons(container: AppContainer) -> list[tuple[str, str]]:
    """Collect (label, callback_data) buttons from enabled modules' admin_menu hooks."""
    # Lazy imports: hooks/menu_render pull the module layer, avoid an import cycle at load.
    from src.bot.hooks import run_hooks
    from src.bot.menu_render import _hook_buttons

    return _hook_buttons(await run_hooks("admin_menu", container=container))


_TITLE = "🛠 <b>Админ-панель</b>\n\nВсё управление ботом — прямо здесь."


@router.message(Command("admin"))
async def cmd_admin(message: Message, container: AppContainer) -> None:
    menu = _menu(await _admin_url(container), await _extra_buttons(container))
    await message.answer(_TITLE, reply_markup=menu, parse_mode="HTML")


@router.callback_query(F.data == "admin:menu")
async def admin_menu(cb: CallbackQuery, container: AppContainer) -> None:
    menu = _menu(await _admin_url(container), await _extra_buttons(container))
    await show_screen(cb, _TITLE, menu)
    await cb.answer()


@router.message(Command("resetmenu"))
async def cmd_resetmenu(message: Message, container: AppContainer) -> None:
    """Reset the bot menu to the lean built-in default."""
    from src.web.routes.admin.menu import _default_menu_rows

    async with container.uow() as uow:
        await uow.menu_nodes.delete_by()
        for row in _default_menu_rows():
            await uow.menu_nodes.add(row)
        await uow.commit()
    await message.answer("✅ Меню сброшено к базовому. Открой /start — увидишь новый вид.")
