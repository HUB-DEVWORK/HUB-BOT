"""Global aiogram error handler: report to telemetry, answer the user gently.

Without it an exception in any handler is only logged and the user stares at a
silent bot. Now every crash produces a short error id — the user sees it and can
quote it to support; the same id arrives at the vendor ingest with the traceback.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types.error_event import ErrorEvent

from src.core.logging import get_logger

if TYPE_CHECKING:
    from aiogram import Dispatcher

    from src.infrastructure.di import AppContainer

log = get_logger(__name__)

_USER_TEXT = "⚠️ Что-то пошло не так. Попробуйте ещё раз.\nКод ошибки: {error_id}"


# Benign TelegramBadRequest messages: the update is stale or the target is already gone —
# nothing the handler did wrong. Matched case-insensitively on the message text.
_BENIGN_BAD_REQUEST = (
    "message is not modified",
    "query is too old",  # user tapped a stale button / answer arrived >15s late (slow box)
    "query id is invalid",
    "message to delete not found",
    "message to edit not found",
    "message can't be deleted",
    "message can't be edited",
    "message to be replied not found",
)


def _is_transient(exc: BaseException) -> bool:
    """Expected Telegram-transport hiccups, not bugs: flood-wait, the user blocked the
    bot, a stale callback query, or an edit that changes nothing. They must not spam
    telemetry/admins nor scare the user with an error id — the offending handler already
    did its job or never could.
    """
    if isinstance(exc, TelegramRetryAfter | TelegramForbiddenError):
        return True
    if isinstance(exc, TelegramBadRequest):
        msg = str(exc).lower()
        return any(s in msg for s in _BENIGN_BAD_REQUEST)
    return False


def setup_error_handler(dp: Dispatcher, container: AppContainer) -> None:
    @dp.errors()
    async def _on_error(event: ErrorEvent) -> bool:
        if _is_transient(event.exception):
            return True  # handled: swallow silently — no telemetry, no user-facing error

        update = event.update
        context: dict[str, Any] = {}
        message = getattr(update, "message", None)
        callback = getattr(update, "callback_query", None)
        if callback is not None and callback.data:
            context["callback_data"] = str(callback.data)[:64]
        if message is not None and message.text and message.text.startswith("/"):
            context["command"] = message.text.split()[0][:32]

        error_id = container.telemetry.report(event.exception, source="bot", context=context)
        log.error("unhandled bot error", error_id=error_id, exc_info=event.exception)

        # Also DM the shop's own admins, so a self-hoster with telemetry disabled still
        # learns of the crash. Best-effort — a notifier failure must not raise here.
        with suppress(Exception):
            await container.notifier.notify_admins(
                f"⚠️ Ошибка в боте (код <code>{error_id}</code>):\n"
                f"{type(event.exception).__name__}: {str(event.exception)[:500]}",
                topic="bug",
            )

        text = _USER_TEXT.format(error_id=error_id)
        with suppress(Exception):  # notifying the user must not raise a second time
            if callback is not None:
                await callback.answer(text, show_alert=True)
            elif message is not None:
                await message.answer(text)
        return True  # handled — aiogram must not re-log or crash the polling loop
