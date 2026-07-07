"""Bot middlewares: DI container, user upsert with attribution, maintenance gate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TgUser

from src.application.events import UserRegistered
from src.application.services.ids import generate_referral_code
from src.core.enums import Locale, Role, UserStatus
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def _tg_user(event: TelegramObject) -> TgUser | None:
    if isinstance(event, Message | CallbackQuery):
        return event.from_user
    return getattr(event, "from_user", None)


class ContextMiddleware(BaseMiddleware):
    """Injects the container and the upserted DB user; gates maintenance mode.

    The DB user is refreshed on every update (names/username drift), created on first
    contact. Attribution (referral / campaign deep-links) is handled by the /start
    handler — this middleware only guarantees the row exists.
    """

    def __init__(self, container: AppContainer) -> None:
        self.container = container

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        data["container"] = self.container
        tg = _tg_user(event)
        if tg is None or tg.is_bot:
            return await handler(event, data)

        async with self.container.uow() as uow:
            user = await uow.users.get_by_telegram_id(tg.id)
            created = False
            if user is None:
                user = User(
                    telegram_id=tg.id,
                    username=tg.username,
                    first_name=tg.first_name,
                    last_name=tg.last_name,
                    language=Locale.EN if (tg.language_code or "ru")[:2] == "en" else Locale.RU,
                    referral_code=generate_referral_code(),
                )
                await uow.users.add(user)
                created = True
            else:
                user.username = tg.username
                user.first_name = tg.first_name
                user.last_name = tg.last_name

            cfg = self.container.bot_config
            maintenance = bool(await cfg.value(uow, "MAINTENANCE_MODE"))
            admin_ids = self._admin_ids(str(await cfg.value(uow, "ADMIN_IDS")))
            maintenance_text = str(await cfg.value(uow, "MAINTENANCE_MESSAGE"))
            await uow.commit()

        if created:
            # Instant "registrations" report + future side-effects (bus is best-effort).
            await self.container.event_bus.publish(
                UserRegistered(user_id=user.id, telegram_id=tg.id)
            )

        is_admin = (
            tg.id in admin_ids
            or tg.id in self.container.settings.app.owner_ids
            or user.role.value >= Role.ADMIN.value
        )
        if user.status is UserStatus.BLOCKED and not is_admin:
            return None  # blocked users are ignored entirely
        if maintenance and not is_admin:
            if isinstance(event, Message):
                await event.answer(maintenance_text)
            elif isinstance(event, CallbackQuery):
                # callback alerts are capped at 200 chars — a longer admin text 400s
                alert = maintenance_text
                if len(alert) > 200:
                    alert = alert[:197] + "…"
                await event.answer(alert, show_alert=True)
            return None

        data["db_user"] = user
        data["db_user_created"] = created
        data["is_admin"] = is_admin
        return await handler(event, data)

    @staticmethod
    def _admin_ids(raw: str) -> set[int]:
        out: set[int] = set()
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out
