"""Support tickets in the bot: create + converse (mirrors admin screen 11).

A user has at most one open/waiting ticket; new messages append to it. Replies from
the cabinet arrive via the admin API (which DMs the user); messages sent here while a
ticket is open are appended and flip the status back to OPEN.
"""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import simple_keyboard
from src.core.enums import TicketAuthor, TicketStatus
from src.infrastructure.database.models.ticket import Ticket, TicketMessage
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

router = Router(name="tickets")


class TicketForm(StatesGroup):
    waiting_text = State()


async def begin_ticket(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """Entry from the support action: show the open ticket or start a new one."""
    async with container.uow() as uow:
        open_tickets = await uow.tickets.list(user_id=db_user.id)
        active = next((t for t in open_tickets if t.status is not TicketStatus.CLOSED), None)
    if active is not None:
        text = (
            f"Тикет <b>#{active.id}</b> · {active.subject}\n\n"
            "Просто напиши сообщение — мы ответим здесь же."
        )
    else:
        text = "Опиши проблему одним сообщением — создадим тикет и ответим здесь."
    if cb.message is not None:
        await cb.message.edit_text(  # type: ignore[union-attr]
            text,
            reply_markup=simple_keyboard([("‹ Меню", "nav:root")]),
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(Command("support"))
async def cmd_support(message: Message, container: AppContainer, db_user: User) -> None:
    await message.answer("Опиши проблему одним сообщением — создадим тикет и ответим здесь.")


@router.message(F.text & ~F.text.startswith("/"))
async def user_message(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    """Plain text outside flows: append to an open ticket, or open a new one."""
    if await state.get_state() is not None:
        return  # user is mid-FSM (e.g. entering a promocode) — don't hijack their input
    text = (message.text or "").strip()
    if not text:
        return
    async with container.uow() as uow:
        cfg = container.bot_config
        mode = str(await cfg.value(uow, "SUPPORT_MODE"))
        support_chat = str(await cfg.value(uow, "SUPPORT_CHAT_ID") or "")
        if mode == "redirect":
            return  # support goes to an external account; ignore free text
        tickets = await uow.tickets.list(user_id=db_user.id)
        active = next((t for t in tickets if t.status is not TicketStatus.CLOSED), None)
        created = False
        if active is None:
            active = Ticket(user_id=db_user.id, subject=text[:64])
            await uow.tickets.add(active)
            created = True
        await uow.ticket_messages.add(
            TicketMessage(ticket_id=active.id, author=TicketAuthor.USER, text=text[:4096])
        )
        active.status = TicketStatus.OPEN
        await uow.commit()
        ticket_id = active.id

    if created:
        await message.answer(
            f"🆗 Тикет <b>#{ticket_id}</b> создан — ответим здесь.", parse_mode="HTML"
        )
    else:
        await message.answer("Добавил к тикету ✍️")

    # Mirror into the support group when configured.
    if support_chat.lstrip("-").isdigit():
        # Group misconfiguration must not break the user flow.
        with contextlib.suppress(Exception):
            await message.bot.send_message(  # type: ignore[union-attr]
                int(support_chat),
                f"🎫 #{ticket_id} от @{db_user.username or db_user.telegram_id}:\n\n{text[:1000]}",
            )
