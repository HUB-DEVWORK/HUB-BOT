"""Admin: support tickets + channel config (screen 11).

Replying stores the message and (best-effort) delivers it to the user via the bot.
Support channel modes (in-bot tickets / redirect / separate bot / mini-app chat) are
bot-config params surfaced here for the screen's config cards.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.core.enums import TicketAuthor, TicketStatus
from src.core.logging import get_logger
from src.infrastructure.database.base import utcnow
from src.infrastructure.database.models.ticket import TicketMessage
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

log = get_logger(__name__)

router = APIRouter()

_CHANNEL_KEYS = ("SUPPORT_MODE", "SUPPORT_REDIRECT_USERNAME", "SUPPORT_BOT_TOKEN")


@router.get("/tickets")
async def list_tickets(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    from sqlalchemy import func, select

    from src.infrastructure.database.models.ticket import Ticket, TicketMessage
    from src.infrastructure.database.models.user import User

    async with container.uow() as uow:
        counts = (
            select(TicketMessage.ticket_id, func.count().label("cnt"))
            .group_by(TicketMessage.ticket_id)
            .subquery()
        )
        stmt = (
            select(Ticket, User.username, func.coalesce(counts.c.cnt, 0))
            .join(User, User.id == Ticket.user_id)
            .outerjoin(counts, counts.c.ticket_id == Ticket.id)
            .order_by(Ticket.updated_at.desc())
            .limit(100)
        )
        rows = [
            {
                "id": t.id,
                "user_id": t.user_id,
                "username": username,
                "subject": t.subject,
                "status": t.status.value,
                "messages": int(cnt),
                "updated_at": iso(t.updated_at),
            }
            for t, username, cnt in (await uow.session.execute(stmt)).all()
        ]
        open_count = await uow.tickets.open_count()
    return {"items": rows, "open_count": open_count}


@router.get("/tickets/{ticket_id}")
async def ticket_detail(
    ticket_id: int, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        t = await uow.tickets.get(ticket_id)
        if t is None:
            raise HTTPException(404, "ticket not found")
        user = await uow.users.get(t.user_id)
        messages = await uow.ticket_messages.list(ticket_id=ticket_id)
        return {
            "id": t.id,
            "subject": t.subject,
            "status": t.status.value,
            "user": {
                "id": t.user_id,
                "username": user.username if user else None,
                "telegram_id": user.telegram_id if user else None,
            },
            "messages": [
                {
                    "id": m.id,
                    "author": m.author.value,
                    "text": m.text,
                    "at": iso(m.created_at),
                }
                for m in messages
            ],
        }


class ReplyIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)


@router.post("/tickets/{ticket_id}/reply")
async def reply_ticket(
    ticket_id: int,
    body: ReplyIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        t = await uow.tickets.get(ticket_id)
        if t is None:
            raise HTTPException(404, "ticket not found")
        user = await uow.users.get(t.user_id)
        await uow.ticket_messages.add(
            TicketMessage(ticket_id=ticket_id, author=TicketAuthor.ADMIN, text=body.text)
        )
        t.status = TicketStatus.WAITING
        await audit(uow, identity, "ticket.reply", f"ticket:{ticket_id}")
        await uow.commit()

    # Best-effort delivery to the user's chat (bot process not required).
    if user is not None and user.telegram_id:
        try:
            from aiogram import Bot

            bot = Bot(token=container.settings.bot.token)
            try:
                await bot.send_message(
                    user.telegram_id,
                    f"💬 Ответ поддержки по тикету #{ticket_id}:\n\n{body.text}",
                )
            finally:
                await bot.session.close()
        except Exception as exc:
            log.warning("ticket reply delivery failed", ticket_id=ticket_id, error=str(exc))
    return OkOut()


class StatusIn(BaseModel):
    status: TicketStatus


@router.patch("/tickets/{ticket_id}/status")
async def set_ticket_status(
    ticket_id: int,
    body: StatusIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        t = await uow.tickets.get(ticket_id)
        if t is None:
            raise HTTPException(404, "ticket not found")
        t.status = body.status
        t.closed_at = utcnow() if body.status is TicketStatus.CLOSED else None
        await audit(uow, identity, "ticket.status", f"ticket:{ticket_id}", status=body.status.value)
        await uow.commit()
    return OkOut()


@router.get("/support-channels")
async def get_support_channels(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        cfg = container.bot_config
        mode = await cfg.value(uow, "SUPPORT_MODE")
        redirect = await cfg.value(uow, "SUPPORT_REDIRECT_USERNAME")
        bot_token = await cfg.value(uow, "SUPPORT_BOT_TOKEN")
    return {
        "mode": mode,
        "redirect_username": redirect,
        "bot_token_set": bool(bot_token),
    }


class ChannelsIn(BaseModel):
    mode: str | None = Field(None, pattern="^(tickets|redirect|bot|miniapp)$")
    redirect_username: str | None = Field(None, max_length=64)
    bot_token: str | None = Field(None, max_length=128)


@router.patch("/support-channels")
async def patch_support_channels(
    body: ChannelsIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    changes: dict[str, Any] = {}
    if body.mode is not None:
        changes["SUPPORT_MODE"] = body.mode
    if body.redirect_username is not None:
        changes["SUPPORT_REDIRECT_USERNAME"] = body.redirect_username.lstrip("@")
    if body.bot_token is not None:
        changes["SUPPORT_BOT_TOKEN"] = body.bot_token
    if not changes:
        raise HTTPException(400, "no changes")
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, changes)
        await audit(uow, identity, "support.channels", None, keys=sorted(changes))
        await uow.commit()
    return OkOut()
