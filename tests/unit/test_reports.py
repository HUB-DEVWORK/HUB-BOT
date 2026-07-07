"""Report topics (screen 14): delivery gating + instant event wiring.

Telegram delivery is stubbed at ``reports._deliver`` — the tests assert what would be
sent and where (group + forum thread), not the network call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from src.application.events import TicketOpened
from src.application.services.bot_config import BotConfigService
from src.infrastructure import events as bus_module
from src.infrastructure.database.models.report_topic import ReportTopic
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.services import reports
from src.infrastructure.taskiq.tasks import _parse_weekly_schedule

if TYPE_CHECKING:
    from src.infrastructure.di import AppContainer


def _container(uow: UnitOfWork, token: str = "42:TEST") -> AppContainer:
    """Minimal container stand-in with the attributes the reports module touches."""
    return cast(
        "AppContainer",
        SimpleNamespace(
            uow=lambda: uow,
            bot_config=BotConfigService(None),
            settings=SimpleNamespace(bot=SimpleNamespace(token=token)),
            event_bus=bus_module.InProcessEventBus(),
        ),
    )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int | None, str]]:
    calls: list[tuple[int, int | None, str]] = []

    async def fake_deliver(token, chat_id, thread_id, text, document):  # type: ignore[no-untyped-def]
        calls.append((chat_id, thread_id, text))

    monkeypatch.setattr(reports, "_deliver", fake_deliver)
    return calls


def test_fmt_amount() -> None:
    assert reports.fmt_amount(1234500) == "12 345 ₽"
    assert reports.fmt_amount(199, "USD") == "1.99 $"
    assert reports.fmt_amount(0) == "0 ₽"


def test_parse_weekly_schedule() -> None:
    assert _parse_weekly_schedule("Mon 10:00") == (0, "10:00")
    assert _parse_weekly_schedule("вс 09:30") == (6, "09:30")
    assert _parse_weekly_schedule("21:00") == (None, "21:00")
    assert _parse_weekly_schedule("") == (None, "")


async def test_send_gated_by_toggle_and_group(
    uow: UnitOfWork, sent: list[tuple[int, int | None, str]]
) -> None:
    container = _container(uow)
    async with uow:
        await uow.report_topics.add(ReportTopic(code="payments", topic_id=7, enabled=False))
        await uow.commit()

    # Disabled topic — nothing goes out even with a group configured.
    async with uow:
        await uow.bot_config.upsert("REPORT_GROUP_ID", "-100555")
        await uow.commit()
    assert await reports.send_topic_report(container, "payments", "hi") is False
    assert sent == []

    async with uow:
        topic = next(t for t in await uow.report_topics.list() if t.code == "payments")
        topic.enabled = True
        await uow.commit()
    assert await reports.send_topic_report(container, "payments", "hi") is True
    assert sent == [(-100555, 7, "hi")]

    # Unknown code and unconfigured group are silent no-ops.
    assert await reports.send_topic_report(container, "nonexistent", "hi") is False
    assert len(sent) == 1


async def test_missing_group_blocks_send(
    uow: UnitOfWork, sent: list[tuple[int, int | None, str]]
) -> None:
    container = _container(uow)
    async with uow:
        await uow.report_topics.add(ReportTopic(code="tickets", topic_id=None, enabled=True))
        await uow.commit()
    assert await reports.send_topic_report(container, "tickets", "hi") is False
    assert sent == []


async def test_ticket_event_reports_into_thread(
    uow: UnitOfWork, sent: list[tuple[int, int | None, str]]
) -> None:
    container = _container(uow)
    async with uow:
        await uow.report_topics.add(ReportTopic(code="tickets", topic_id=3, enabled=True))
        await uow.bot_config.upsert("REPORT_GROUP_ID", "-100777")
        await uow.commit()

    reports.wire_report_events(container)
    await container.event_bus.publish(
        TicketOpened(
            ticket_id=12,
            user_id=1,
            telegram_id=111,
            username="vasya",
            subject="<b>help</b>",
        )
    )
    assert len(sent) == 1
    chat_id, thread_id, text = sent[0]
    assert (chat_id, thread_id) == (-100777, 3)
    assert "#12" in text and "@vasya" in text
    assert "&lt;b&gt;help&lt;/b&gt;" in text  # user text is HTML-escaped
