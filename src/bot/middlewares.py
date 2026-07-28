"""Bot middlewares: DI container, user upsert with attribution, maintenance gate."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject
from aiogram.types import User as TgUser

from src.application.events import UserRegistered
from src.application.services.ids import generate_referral_code
from src.core.enums import Locale, Role, UserStatus
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AbortFormOnCommand(BaseMiddleware):
    """A slash-command is top-level navigation, so it must abort any pending form (promocode /
    withdrawal-details input). Otherwise the FSM state survives (RedisStorage) and the user's
    NEXT stray message is captured by the form handler — e.g. typed as withdrawal details and
    charged. Runs as an INNER middleware so ``state`` is already resolved into ``data``.
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, Message) and (event.text or "").startswith("/"):
            state = data.get("state")
            if state is not None and await state.get_state() is not None:
                await state.clear()
        return await handler(event, data)


class UserMessageCleanupMiddleware(BaseMiddleware):
    """Chat cleanup: after the handler runs, delete the user's own incoming message so only the
    bot's current screen is left in the chat. Opt-in via MESSAGE_CLEANUP_ENABLED, OFF by default,
    so an existing bot is unchanged until the owner turns it on. Best-effort: a delete that fails
    (message too old / already gone / no rights) is swallowed. A successful-payment service
    message is never deleted.
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        result = await handler(event, data)
        if not isinstance(event, Message) or event.successful_payment is not None:
            return result
        container: AppContainer | None = data.get("container")
        if container is None:
            return result
        try:
            async with container.uow() as uow:
                enabled = bool(await container.bot_config.value(uow, "MESSAGE_CLEANUP_ENABLED"))
        except Exception:
            return result
        if enabled:
            with contextlib.suppress(Exception):
                await event.delete()
        return result


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
            # Race-safe: a rapid /start burst delivers several updates for the same new
            # user at once; get_or_create makes the losers re-read instead of crashing on
            # the duplicate telegram_id.
            user, created = await uow.users.get_or_create(
                User(
                    telegram_id=tg.id,
                    username=tg.username,
                    first_name=tg.first_name,
                    last_name=tg.last_name,
                    language=Locale.EN if (tg.language_code or "ru")[:2] == "en" else Locale.RU,
                    referral_code=generate_referral_code(),
                )
            )
            if not created:
                user.username = tg.username
                user.first_name = tg.first_name
                user.last_name = tg.last_name

            cfg = self.container.bot_config
            maintenance = bool(await cfg.value(uow, "MAINTENANCE_MODE"))
            admin_ids = self._admin_ids(str(await cfg.value(uow, "ADMIN_IDS")))
            maintenance_text = str(await cfg.value(uow, "MAINTENANCE_MESSAGE"))
            blacklist_on = bool(await cfg.value(uow, "BLACKLIST_CHECK_ENABLED"))
            blacklisted = blacklist_on and await uow.blacklist.has(tg.id)
            rate_on = bool(await cfg.value(uow, "RATE_LIMIT_ENABLED"))
            cooldown = int(await cfg.value(uow, "RATE_LIMIT_COOLDOWN_SEC"))
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
        # Payment-settlement updates must never be silently dropped: an ignored
        # successful_payment = charged stars with no credit, an unanswered pre_checkout
        # breaks the payment Telegram-side after a 10s timeout. Blocked/blacklisted users
        # are refused at pre_checkout (BEFORE money moves); their already-charged
        # successful_payment still settles.
        settlement_msg = isinstance(event, Message) and event.successful_payment is not None
        is_pre_checkout = isinstance(event, PreCheckoutQuery)

        if (user.status is UserStatus.BLOCKED or blacklisted) and not is_admin:
            if isinstance(event, PreCheckoutQuery):
                await event.answer(ok=False, error_message="Оплата недоступна.")
            if not settlement_msg:
                return None  # ignored entirely — except a payment that already happened
        if (
            rate_on
            and not is_admin
            and not settlement_msg
            and not is_pre_checkout
            and cooldown > 0
            and not await self.container.redis.set(f"rl:{tg.id}", "1", nx=True, ex=cooldown)
        ):
            # Flood control — drop the update, but ALWAYS answer a callback: an
            # unanswered tap leaves the button with an eternal spinner.
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None
        if maintenance and not is_admin and not settlement_msg:
            if isinstance(event, PreCheckoutQuery):
                # Refuse BEFORE the charge — money must not enter during maintenance.
                await event.answer(ok=False, error_message="Технические работы — попробуй позже.")
            elif isinstance(event, Message):
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
