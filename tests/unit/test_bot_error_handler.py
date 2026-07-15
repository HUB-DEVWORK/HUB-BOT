"""Global aiogram error handler: swallow transient Telegram errors silently, and for
genuine bugs report to telemetry AND ping the shop's own admins (src/bot/errors.py)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from src.bot.errors import _is_transient, setup_error_handler


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def report(
        self, exc: BaseException, *, source: str, context: dict[str, Any] | None = None
    ) -> str:
        self.calls.append({"exc": exc, "source": source, "context": context})
        return "Edeadbeef"


class _RecordingNotifier:
    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._boom = boom

    async def notify_admins(self, text: str, *, topic: str | None = None) -> None:
        if self._boom:
            raise RuntimeError("notifier down")
        self.calls.append((text, topic))


class _Recorder:
    """An async stand-in method that records each ``(args, kwargs)`` call."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


def _container() -> Any:
    return SimpleNamespace(telemetry=_RecordingTelemetry(), notifier=_RecordingNotifier())


def _handler(container: Any) -> Any:
    dp = Dispatcher()
    setup_error_handler(dp, container)
    return dp.errors.handlers[-1].callback


def _event(exc: BaseException, *, message: Any = None, callback: Any = None) -> SimpleNamespace:
    # The handler only touches ``event.update`` / ``event.exception`` via getattr, so a
    # lightweight namespace stands in for a real (validation-heavy) aiogram Update.
    update = SimpleNamespace(message=message, callback_query=callback)
    return SimpleNamespace(update=update, exception=exc)


def _retry_after() -> TelegramRetryAfter:
    return TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=5)  # type: ignore[arg-type]


def _forbidden() -> TelegramForbiddenError:
    return TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")  # type: ignore[arg-type]


def _not_modified() -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message="Bad Request: message is not modified")  # type: ignore[arg-type]


def _stale_query() -> TelegramBadRequest:
    # The exact error a slow 1 GB box throws on a late callback answer (E6004 in the wild).
    return TelegramBadRequest(  # type: ignore[arg-type]
        method=None,
        message="Bad Request: query is too old and response timeout expired or query ID is invalid",
    )


def test_is_transient_classification() -> None:
    assert _is_transient(_retry_after()) is True
    assert _is_transient(_forbidden()) is True
    assert _is_transient(_not_modified()) is True
    assert _is_transient(_stale_query()) is True  # stale callback — benign, not a bug
    for msg in ("message to delete not found", "message to edit not found"):
        assert _is_transient(TelegramBadRequest(method=None, message=msg)) is True  # type: ignore[arg-type]
    # A genuine bug and an *unrelated* bad-request must not be swallowed.
    assert _is_transient(RuntimeError("boom")) is False
    assert _is_transient(TelegramBadRequest(method=None, message="chat not found")) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("exc_factory", [_retry_after, _forbidden, _not_modified])
async def test_transient_errors_are_swallowed_silently(exc_factory) -> None:  # type: ignore[no-untyped-def]
    container = _container()
    handler = _handler(container)
    msg = SimpleNamespace(text=None, answer=_Recorder())

    result = await handler(_event(exc_factory(), message=msg))

    assert result is True  # handled — polling loop must not crash
    assert container.telemetry.calls == []  # no telemetry noise
    assert container.notifier.calls == []  # no admin ping
    assert msg.answer.calls == []  # user never sees a scary error


async def test_genuine_error_reports_and_pings_admins() -> None:
    container = _container()
    handler = _handler(container)
    msg = SimpleNamespace(text="/buy", answer=_Recorder())

    result = await handler(_event(RuntimeError("boom"), message=msg))

    assert result is True
    assert len(container.telemetry.calls) == 1
    assert container.telemetry.calls[0]["source"] == "bot"
    assert container.telemetry.calls[0]["context"] == {"command": "/buy"}
    # Self-hoster with telemetry disabled still learns of the crash via their own admins.
    assert len(container.notifier.calls) == 1
    text, topic = container.notifier.calls[0]
    assert topic == "bug"
    assert "Edeadbeef" in text and "RuntimeError" in text
    # And the user gets the gentle message with the quotable id.
    assert msg.answer.calls and "Edeadbeef" in msg.answer.calls[0][0][0]


async def test_genuine_error_on_callback_uses_alert() -> None:
    container = _container()
    handler = _handler(container)
    cb = SimpleNamespace(data="pay:1:2:bal", answer=_Recorder())

    result = await handler(_event(RuntimeError("boom"), callback=cb))

    assert result is True
    assert container.telemetry.calls[0]["context"] == {"callback_data": "pay:1:2:bal"}
    assert cb.answer.calls and cb.answer.calls[0][1].get("show_alert") is True


async def test_admin_notify_failure_is_suppressed() -> None:
    container = SimpleNamespace(
        telemetry=_RecordingTelemetry(), notifier=_RecordingNotifier(boom=True)
    )
    handler = _handler(container)
    msg = SimpleNamespace(text=None, answer=_Recorder())

    # A failing notifier must not raise a second time out of the error handler.
    assert await handler(_event(RuntimeError("boom"), message=msg)) is True
