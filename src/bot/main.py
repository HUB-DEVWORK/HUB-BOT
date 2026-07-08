"""Bot entrypoint: ``python -m src.bot.main`` (long polling) or webhook via web app.

Owns its AppContainer (same graph as web/worker). Colored menu buttons and the mini-app
menu button are configured at startup from bot-config.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage

from src.bot.handlers import build_router
from src.bot.middlewares import AbortFormOnCommand, ContextMiddleware
from src.core.config import get_settings
from src.core.logging import configure_logging, get_logger
from src.infrastructure.di import AppContainer

log = get_logger(__name__)


async def _apply_bot_config(bot: Bot, container: AppContainer) -> None:
    """Sync BOT_USERNAME + the mini-app menu button from the live config."""
    me = await bot.get_me()
    async with container.uow() as uow:
        cfg = container.bot_config
        if str(await cfg.value(uow, "BOT_USERNAME") or "") != (me.username or ""):
            await cfg.set_values(uow, {"BOT_USERNAME": me.username or ""})
            await uow.commit()
        miniapp_url = str(await cfg.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        # Auto-wire the mini-app from WEB__PUBLIC_URL if the owner hasn't set a URL, so the
        # chat menu button + WebApp buttons appear out of the box (same value the web boot sets).
        public = (container.settings.web.public_url or "").strip().rstrip("/")
        if not miniapp_url and public.startswith("https://"):
            miniapp_url = f"{public}/app"
            await cfg.set_values(uow, {"SUBSCRIPTION_MINI_APP_URL": miniapp_url})
            await uow.commit()
    if miniapp_url.startswith("https://"):
        from aiogram.types import MenuButtonWebApp, WebAppInfo

        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="VPN", web_app=WebAppInfo(url=miniapp_url))
        )


async def run() -> None:
    settings = get_settings()
    configure_logging(level=settings.log.level, json=settings.log.use_json)
    container = AppContainer(settings)

    bot = Bot(token=settings.bot.token, default=DefaultBotProperties(parse_mode=None))
    storage = RedisStorage(container.redis)
    dp = Dispatcher(storage=storage)
    # Attach to the user-bearing observers (an Update wrapper has no `from_user`, so a
    # single dp.update middleware never resolves the DB user and handlers lose `db_user`).
    context = ContextMiddleware(container)
    dp.message.outer_middleware(context)
    dp.callback_query.outer_middleware(context)
    # Inner (post-FSM-resolution): a command aborts a pending form so a later stray message
    # can't be captured as promocode/withdrawal input.
    dp.message.middleware(AbortFormOnCommand())
    dp.include_router(build_router())

    await _apply_bot_config(bot, container)
    log.info("bot starting (long polling)")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await container.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
