"""Broadcasts: pick audience -> enter text -> confirm -> enqueue the taskiq job.

Mirrors the cabinet's create_broadcast: build a Broadcast(PENDING) row with the audience
size as total, commit, then send_broadcast.kiq(id). Text-only from the chat (media &
buttons stay in the web cabinet).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from src.bot.handlers.admin._common import MENU_CB, back_kb, rows_kb
from src.bot.handlers.reply_menu import maybe_dispatch_menu_button
from src.bot.screen import ack, show_screen
from src.core.enums import BroadcastAudience, BroadcastMedia, BroadcastStatus
from src.infrastructure.database.models.broadcast import Broadcast
from src.infrastructure.database.models.user import User
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.di import AppContainer

router = Router(name="admin-broadcast")

_AUDIENCES: list[tuple[BroadcastAudience, str]] = [
    (BroadcastAudience.ALL, "Все"),
    (BroadcastAudience.ACTIVE, "С активной подпиской"),
    (BroadcastAudience.TRIAL, "На триале"),
    (BroadcastAudience.EXPIRED, "Истёкшие"),
]
_AUD_LABEL = {a.value: label for a, label in _AUDIENCES}


class BroadcastForm(StatesGroup):
    text = State()


async def _audience_size(uow: UnitOfWork, audience: BroadcastAudience) -> int:
    from src.web.routes.admin.broadcasts import audience_stmt

    stmt = select(func.count()).select_from(audience_stmt(audience).subquery())
    return int(await uow.session.scalar(stmt) or 0)


@router.callback_query(F.data == "admin:bc")
async def bc_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    kb = rows_kb(
        [[(label, f"admin:bc:a:{a.value}")] for a, label in _AUDIENCES] + [[("‹ Назад", MENU_CB)]]
    )
    await show_screen(cb, "📣 <b>Рассылка</b>\n\nКому отправляем?", kb)
    await cb.answer()


@router.callback_query(F.data.startswith("admin:bc:a:"))
async def bc_audience(cb: CallbackQuery, container: AppContainer, state: FSMContext) -> None:
    value = (cb.data or "").rsplit(":", 1)[-1]
    if value not in _AUD_LABEL:
        await cb.answer()
        return
    async with container.uow() as uow:
        size = await _audience_size(uow, BroadcastAudience(value))
    if size == 0:
        await cb.answer("В этой аудитории никого нет", show_alert=True)
        return
    await state.set_state(BroadcastForm.text)
    await state.update_data(audience=value)
    await show_screen(
        cb,
        f"📣 <b>{_AUD_LABEL[value]}</b> · получателей: <b>{size}</b>\n\n"
        f"Пришли текст сообщения одним сообщением. HTML-разметка поддерживается.",
        back_kb("admin:bc"),
    )
    await ack(cb)


@router.message(BroadcastForm.text, F.text)
async def bc_text(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Пришли сообщение для рассылки.")
        return
    data = await state.get_data()
    audience = str(data.get("audience") or BroadcastAudience.ALL.value)
    await state.update_data(text=text)
    async with container.uow() as uow:
        size = await _audience_size(uow, BroadcastAudience(audience))
    preview = text if len(text) <= 500 else text[:500] + "…"
    kb = rows_kb(
        [
            [("✅ Отправить", "admin:bc:go"), ("‹ Отмена", MENU_CB)],
        ]
    )
    await message.answer(
        f"📣 <b>Проверь рассылку</b>\n"
        f"Аудитория: {_AUD_LABEL.get(audience, audience)} · получателей: <b>{size}</b>\n\n"
        f"{preview}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:bc:go")
async def bc_send(
    cb: CallbackQuery, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    data = await state.get_data()
    text = str(data.get("text") or "").strip()
    audience = str(data.get("audience") or BroadcastAudience.ALL.value)
    await state.clear()
    if not text:
        await cb.answer("Нечего отправлять", show_alert=True)
        return
    async with container.uow() as uow:
        total = await _audience_size(uow, BroadcastAudience(audience))
        if total == 0:
            await cb.answer("Аудитория опустела", show_alert=True)
            return
        broadcast = await uow.broadcasts.add(
            Broadcast(
                audience=BroadcastAudience(audience),
                media=BroadcastMedia.TEXT,
                text=text,
                status=BroadcastStatus.PENDING,
                total=total,
                created_by_id=db_user.id,
            )
        )
        await uow.commit()
        broadcast_id = broadcast.id
    from src.infrastructure.taskiq.tasks import send_broadcast

    await send_broadcast.kiq(broadcast_id)
    await show_screen(
        cb,
        f"🚀 Рассылка запущена на {total} получателей.\n"
        f"Прогресс и статистика — в веб-админке → «Рассылки».",
        back_kb(MENU_CB),
    )
    await cb.answer("Запущено")
