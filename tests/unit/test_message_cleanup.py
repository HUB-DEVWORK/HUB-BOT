"""UserMessageCleanupMiddleware: opt-in deletion of the user's message after handling.

The middleware guards on ``isinstance(event, Message)``; the tests patch that symbol to the
fake message class so a lightweight stand-in exercises the real delete path.
"""

from __future__ import annotations

from typing import Any

import pytest

import src.bot.middlewares as mw_mod
from src.bot.middlewares import UserMessageCleanupMiddleware


class _FakeBotConfig:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    async def value(self, _uow: Any, key: str) -> Any:
        assert key == "MESSAGE_CLEANUP_ENABLED"
        return self._enabled


class _FakeUow:
    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeContainer:
    def __init__(self, enabled: bool) -> None:
        self.bot_config = _FakeBotConfig(enabled)

    def uow(self) -> _FakeUow:
        return _FakeUow()


class _FakeMessage:
    """Minimal aiogram Message stand-in: only what the middleware touches."""

    def __init__(self, *, successful_payment: object | None = None) -> None:
        self.successful_payment = successful_payment
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


async def _run(event: Any, *, enabled: bool, monkeypatch: pytest.MonkeyPatch) -> Any:
    # Make the middleware's Message check accept our stand-in.
    monkeypatch.setattr(mw_mod, "Message", _FakeMessage)
    mw = UserMessageCleanupMiddleware()
    data: dict[str, Any] = {"container": _FakeContainer(enabled)}
    calls: list[Any] = []

    async def handler(ev: Any, d: dict[str, Any]) -> str:
        calls.append(ev)
        return "handled"

    result = await mw(handler, event, data)
    assert calls == [event]  # the handler always runs first
    return result


async def test_deletes_user_message_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _FakeMessage()
    result = await _run(msg, enabled=True, monkeypatch=monkeypatch)
    assert result == "handled"
    assert msg.deleted is True


async def test_keeps_message_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _FakeMessage()
    await _run(msg, enabled=False, monkeypatch=monkeypatch)
    assert msg.deleted is False


async def test_never_deletes_successful_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _FakeMessage(successful_payment=object())
    await _run(msg, enabled=True, monkeypatch=monkeypatch)
    assert msg.deleted is False


async def test_non_message_event_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-Message event (e.g. a callback query) must not be touched; handler still runs.
    class _Cb:
        pass

    result = await _run(_Cb(), enabled=True, monkeypatch=monkeypatch)
    assert result == "handled"


async def test_delete_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Brittle(_FakeMessage):
        async def delete(self) -> None:
            raise RuntimeError("message too old")

    msg = _Brittle()
    # Must not raise out of the middleware even though delete() fails.
    result = await _run(msg, enabled=True, monkeypatch=monkeypatch)
    assert result == "handled"
