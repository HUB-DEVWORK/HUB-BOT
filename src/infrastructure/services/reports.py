"""Report-topic delivery into the forum group (admin screen 14).

``report_topics`` binds a report kind to a forum-topic id; the group itself lives in
bot-config (``REPORT_GROUP_ID``). Scheduled summaries (daily/weekly) are composed by the
taskiq job; the instant kinds (payments / tickets / registrations) subscribe to the domain
event bus here — ``wire_report_events`` runs in every process (bot, web, worker), so each
process reports the events it produces. All sends are best-effort: a disabled topic, a
missing group id or a Telegram error never breaks the publishing flow.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

from src.application.common.events import DomainEvent
from src.application.events import PaymentCompleted, TicketOpened, UserRegistered
from src.core.enums import TransactionType
from src.core.logging import get_logger
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from src.infrastructure.di import AppContainer

log = get_logger(__name__)

_CURRENCY_SIGNS = {"RUB": "₽", "USD": "$", "EUR": "€", "XTR": "⭐"}


def fmt_amount(minor: int, currency: str = "RUB") -> str:
    """1234500 minor RUB -> '12 345 ₽'; keeps kopecks only when present."""
    sign = _CURRENCY_SIGNS.get(currency, currency)
    v = minor / 100
    s = f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    return f"{s.replace(',', ' ')} {sign}"


async def send_topic_report(
    container: AppContainer, code: str, text: str, *, document: Path | None = None
) -> bool:
    """Send ``text`` (and optionally a file) into the report-group topic bound to ``code``.

    Returns True only when actually delivered. Skips silently when the topic is disabled,
    the group id is missing/malformed or there is no bot token.
    """
    async with container.uow() as uow:
        group = str(await container.bot_config.value(uow, "REPORT_GROUP_ID") or "").strip()
        dm_admins = bool(await container.bot_config.value(uow, "REPORT_DM_ADMINS"))
        topic = next((t for t in await uow.report_topics.list() if t.code == code), None)
    if topic is None or not topic.enabled or not container.settings.bot.token:
        return False
    delivered = False
    # Group forum topic (when a group id is configured).
    if group.lstrip("-").isdigit():
        try:
            await _deliver(container.settings.bot.token, int(group), topic.topic_id, text, document)
            delivered = True
        except Exception:
            log.warning("report_send_failed", code=code, exc_info=True)
    # Admin DMs (independent destination — works even without a group).
    if dm_admins:
        import contextlib

        with contextlib.suppress(Exception):
            await container.notifier.notify_admins(text)
            delivered = True
    return delivered


async def _deliver(
    token: str, chat_id: int, thread_id: int | None, text: str, document: Path | None
) -> None:
    from aiogram import Bot
    from aiogram.types import FSInputFile

    bot = Bot(token=token)
    try:
        if document is not None:
            await bot.send_document(
                chat_id,
                FSInputFile(document),
                caption=text,
                message_thread_id=thread_id,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id, text, message_thread_id=thread_id, parse_mode="HTML")
    finally:
        await bot.session.close()


# --- instant topics: event-bus subscribers --------------------------------------


def wire_report_events(container: AppContainer) -> None:
    """Subscribe the instant report topics to the domain event bus.

    Called from the container so every process reports the events it publishes:
    payments complete in the worker, registrations/tickets happen in the bot and web.
    """

    async def _on_event(event: DomainEvent) -> None:
        if isinstance(event, PaymentCompleted):
            await _report_payment(container, event)
        elif isinstance(event, UserRegistered):
            await _report_registration(container, event)
        elif isinstance(event, TicketOpened):
            await _report_ticket(container, event)

    container.event_bus.subscribe(_on_event)


def _who(user: User | None) -> str:
    if user is None:
        return "—"
    if user.username:
        return f"@{html.escape(user.username)}"
    return f"id{user.telegram_id or user.id}"


async def _report_payment(container: AppContainer, event: PaymentCompleted) -> None:
    async with container.uow() as uow:
        user = await uow.users.get(event.user_id)
        txn = await uow.transactions.get(event.transaction_id)
    kind = "пополнение баланса"
    if txn is not None and txn.type is TransactionType.SUBSCRIPTION_PAYMENT:
        kind = "оплата подписки"
    test = " · тест" if txn is not None and txn.is_test else ""
    await send_topic_report(
        container,
        "payments",
        f"💳 <b>{fmt_amount(event.amount_minor, event.currency)}</b> · {kind} · {_who(user)}{test}",
    )


async def _report_registration(container: AppContainer, event: UserRegistered) -> None:
    async with container.uow() as uow:
        user = await uow.users.get(event.user_id)
        total = await uow.users.count()
    ref = " · по рефералке" if event.referred_by_id is not None else ""
    await send_topic_report(
        container, "registrations", f"👤 Новый пользователь: {_who(user)}{ref} · всего: {total}"
    )


async def _report_ticket(container: AppContainer, event: TicketOpened) -> None:
    who = (
        f"@{html.escape(event.username)}"
        if event.username
        else f"id{event.telegram_id or event.user_id}"
    )
    await send_topic_report(
        container,
        "tickets",
        f"🎫 Тикет <b>#{event.ticket_id}</b> от {who}:\n{html.escape(event.subject)}",
    )
