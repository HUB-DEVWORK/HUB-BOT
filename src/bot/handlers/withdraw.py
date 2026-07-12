"""Referral-earnings withdrawal: «Вывести» -> method -> details -> request to admin.

The amount (everything available) is debited immediately with the guarded debit, so a
pending request can't be double-spent; a rejected request refunds automatically.
Registered BEFORE tickets so the state-gated details input wins over the catch-all.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.bot.banners import render_screen
from src.bot.keyboards import simple_keyboard
from src.bot.screen import show_screen
from src.core.logging import get_logger
from src.infrastructure.database.models.user import User
from src.infrastructure.database.models.withdrawal import WithdrawalRequest
from src.infrastructure.di import AppContainer

log = get_logger(__name__)

router = Router(name="withdraw")

_METHODS = {"card": "💳 Карта", "usdt": "🪙 USDT (TRC-20)", "ton": "💎 TON"}


class WithdrawForm(StatesGroup):
    waiting_details = State()


async def available_minor(container: AppContainer, user_id: int) -> int:
    """min(баланс, заработано рефералкой минус уже выведенное/в заявках)."""
    from sqlalchemy import func, select

    from src.core.enums import WithdrawalStatus
    from src.infrastructure.database.models.referral import ReferralEarning

    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        if user is None:
            return 0
        earned = int(
            await uow.session.scalar(
                select(func.coalesce(func.sum(ReferralEarning.amount_minor), 0)).where(
                    ReferralEarning.user_id == user_id, ReferralEarning.is_issued.is_(True)
                )
            )
            or 0
        )
        held = int(
            await uow.session.scalar(
                select(func.coalesce(func.sum(WithdrawalRequest.amount_minor), 0)).where(
                    WithdrawalRequest.user_id == user_id,
                    WithdrawalRequest.status.in_((WithdrawalStatus.PENDING, WithdrawalStatus.PAID)),
                )
            )
            or 0
        )
        return max(0, min(user.balance_minor, earned - held))


@router.callback_query(F.data == "withdraw:start")
async def start_withdraw(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        enabled = bool(await container.bot_config.value(uow, "REFERRAL_WITHDRAWAL_ENABLED"))
        min_minor = int(await container.bot_config.value(uow, "REFERRAL_WITHDRAWAL_MIN"))
    if not enabled:
        await cb.answer("Вывод временно недоступен", show_alert=True)
        return
    avail = await available_minor(container, db_user.id)
    if avail < min_minor:
        await cb.answer(
            f"Минимум для вывода — {min_minor / 100:.0f} ₽, доступно {avail / 100:.2f} ₽",
            show_alert=True,
        )
        return
    rows = [(label, f"withdraw:m:{code}") for code, label in _METHODS.items()]
    rows.append(("‹ Назад", "act:referral:0"))
    await render_screen(
        cb,
        container,
        "withdraw",
        f"<b>💸 Вывод заработка</b>\n\nДоступно к выводу: <b>{avail / 100:.2f} ₽</b>\n"
        "Отправим всю сумму разом — выбери, куда:",
        simple_keyboard(rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("withdraw:m:"))
async def choose_method(
    cb: CallbackQuery, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    method = (cb.data or "").rsplit(":", 1)[-1]
    if method not in _METHODS:
        await cb.answer()
        return
    await state.set_state(WithdrawForm.waiting_details)
    await state.update_data(method=method)
    prompt = {
        "card": "Отправь номер карты одним сообщением:",
        "usdt": "Отправь адрес кошелька USDT (TRC-20):",
        "ton": "Отправь адрес TON-кошелька:",
    }[method]
    await show_screen(cb, prompt, simple_keyboard([("‹ Отмена", "nav:root")]), parse_mode=None)
    await cb.answer()


@router.message(WithdrawForm.waiting_details, F.text)
async def take_details(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    from src.bot.handlers.reply_menu import maybe_dispatch_menu_button

    # A bottom-bar tap (reply mode) reaches here before reply_menu — don't take it as details.
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    data = await state.get_data()
    await state.clear()
    method = str(data.get("method") or "card")
    details = (message.text or "").strip()[:256]
    if len(details) < 8:
        await message.answer("Слишком короткие реквизиты — начни заново: Рефералка → Вывести.")
        return

    async with container.uow() as uow:
        min_minor = int(await container.bot_config.value(uow, "REFERRAL_WITHDRAWAL_MIN"))
        user = await uow.users.get(db_user.id)
        if user is None:
            return
        avail = await available_minor(container, user.id)
        if avail < min_minor:
            await message.answer("Сумма уже недоступна — проверь баланс в рефералке.")
            return
        if not await uow.users.debit_balance_guarded(user, avail):
            await message.answer("Баланс изменился — попробуй ещё раз.")
            return
        req = WithdrawalRequest(user_id=user.id, amount_minor=avail, method=method, details=details)
        uow.session.add(req)
        await uow.commit()
        amount, req_id = avail, req.id

    await message.answer(
        f"✅ Заявка #{req_id} на <b>{amount / 100:.2f} ₽</b> создана.\n"
        "Деньги зарезервированы; выплатим после проверки и напишем сюда.",
        parse_mode="HTML",
    )
    await container.notifier.notify_admins(
        f"💸 Заявка на вывод #{req_id}: {amount / 100:.2f} ₽ · {_METHODS[method]}\n"
        f"Юзер: {user.username or user.telegram_id}\nРеквизиты: {details}\n"
        "Обработка: кабинет → Платежи → Выводы.",
        topic="payments",
    )
