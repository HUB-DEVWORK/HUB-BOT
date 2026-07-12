"""Promocode entry in the bot: tap -> type code -> apply (wallet/discount/group rewards).

Registered BEFORE tickets so the state-gated code input wins over the ticket free-text
catch-all. Panel-affecting rewards (duration/traffic/devices/subscription) surface a clear
message telling the user to apply them during purchase.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.application.services.promo import PromoError
from src.bot.banners import render_screen
from src.bot.keyboards import simple_keyboard
from src.bot.screen import ack
from src.core.enums import RewardType
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

router = Router(name="promo")


class PromoForm(StatesGroup):
    waiting_code = State()


_REWARD_TEXT: dict[RewardType, str] = {
    RewardType.BALANCE: "Баланс пополнен.",
    RewardType.PERSONAL_DISCOUNT: "Персональная скидка активирована.",
    RewardType.PURCHASE_DISCOUNT: "Скидка на следующую покупку активирована.",
    RewardType.PROMO_GROUP: "Промо-группа применена.",
    RewardType.DURATION: "Подписка продлена 🎉",
    RewardType.SUBSCRIPTION: "Бесплатные дни подписки начислены 🎉",
}


@router.callback_query(F.data.startswith("act:promocode"))
async def ask_code(
    cb: CallbackQuery | Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    await state.set_state(PromoForm.waiting_code)
    await render_screen(
        cb,
        container,
        "promocode",
        "<b>🎟 Промокод</b>\n\nПришли код одним сообщением — начислим бонус сразу.",
        simple_keyboard([("‹ Меню", "nav:root")]),
    )
    await ack(cb)


@router.message(PromoForm.waiting_code, F.text)
async def apply_code(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    from src.bot.handlers.reply_menu import maybe_dispatch_menu_button

    # A bottom-bar tap (reply mode) reaches here before reply_menu — don't swallow it as a code.
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    await state.clear()
    # Codes are stored uppercase (admin/miniapp normalize input) — the bot must too.
    code = (message.text or "").strip().upper()
    if not code:
        await message.answer("Пустой промокод.")
        return
    async with container.uow() as uow:
        user = await uow.users.get(db_user.id)
        if user is None:
            await message.answer("Ошибка.")
            return
        try:
            reward = await container.promo.apply(uow, user, code)
        except PromoError as exc:
            await message.answer(f"❌ {exc}")
            return
        await uow.commit()
    from src.bot.keyboards import simple_keyboard as _kb

    await message.answer(
        f"✅ Промокод применён. {_REWARD_TEXT.get(reward, '')}".strip(),
        reply_markup=_kb([("🛒 К покупке", "act:buy:0"), ("‹ Меню", "nav:root")]),
    )
