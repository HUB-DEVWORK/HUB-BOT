"""Promocodes, gift-code batches and sale campaigns — create & list from the chat.

Reuses the same DAOs the web cabinet writes through (uow.promocodes / uow.sales); no
business logic is duplicated. Reward semantics mirror the cabinet's _UI_REWARDS:
  balance -> RewardType.BALANCE  (value in ₽, stored as minor units)
  days    -> RewardType.DURATION (value in days added to an active sub)
  trial   -> RewardType.SUBSCRIPTION (value = days of a free granted sub)
"""

from __future__ import annotations

import secrets
import string
from html import escape as hesc

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import func, select

from src.bot.handlers.admin._common import MENU_CB, back_kb, parse_ints, rows_kb
from src.bot.handlers.reply_menu import maybe_dispatch_menu_button
from src.bot.screen import ack, safe_answer, show_screen
from src.core.enums import RewardType
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation
from src.infrastructure.database.models.sale_campaign import SaleCampaign
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

router = Router(name="admin-promos")

_REWARDS: dict[str, tuple[RewardType, str]] = {
    "balance": (RewardType.BALANCE, "Баланс (₽)"),
    "days": (RewardType.DURATION, "Дни к подписке"),
    "trial": (RewardType.SUBSCRIPTION, "Бесплатная подписка (дни)"),
}
_ALPHABET = string.ascii_uppercase + string.digits


def _gen_code(length: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _reward_value(kind: str, raw: int) -> int:
    """UI value -> stored reward_value (₽ -> minor units for balance; days as-is)."""
    return raw * 100 if kind == "balance" else raw


def _reward_label(p: Promocode) -> str:
    if p.reward_type is RewardType.BALANCE:
        return f"{p.reward_value / 100:.0f} ₽"
    if p.reward_type is RewardType.DURATION:
        return f"+{p.reward_value} дн"
    if p.reward_type is RewardType.SUBSCRIPTION:
        return f"подписка {p.reward_value} дн"
    return p.reward_type.value


class PromoForm(StatesGroup):
    value = State()


class GiftForm(StatesGroup):
    value = State()


class SaleForm(StatesGroup):
    value = State()


# --- promocodes ----------------------------------------------------------------


@router.callback_query(F.data == "admin:promo")
async def promo_home(cb: CallbackQuery, container: AppContainer) -> None:
    async with container.uow() as uow:
        promos = list(await uow.promocodes.list(limit=10))
        rows = (
            await uow.session.execute(
                select(PromocodeActivation.promocode_id, func.count()).group_by(
                    PromocodeActivation.promocode_id
                )
            )
        ).all()
        counts: dict[int, int] = {int(pid): int(n) for pid, n in rows}
    lines = ["🎟 <b>Промокоды</b>", ""]
    if not promos:
        lines.append("Пока нет ни одного. Нажми «Создать».")
    for p in promos:
        used = int(counts.get(p.id, 0))
        cap = "∞" if p.max_activations is None else str(p.max_activations)
        flag = "" if p.is_active else " · выкл"
        lines.append(f"<code>{hesc(p.code)}</code> — {_reward_label(p)} · {used}/{cap}{flag}")
    kb = rows_kb([[("➕ Создать", "admin:promo:new")], [("‹ Назад", MENU_CB)]])
    await show_screen(cb, "\n".join(lines), kb)
    await cb.answer()


@router.callback_query(F.data == "admin:promo:new")
async def promo_new(cb: CallbackQuery) -> None:
    kb = rows_kb(
        [[(label, f"admin:promo:k:{k}")] for k, (_, label) in _REWARDS.items()]
        + [[("‹ Назад", "admin:promo")]]
    )
    await show_screen(cb, "🎟 <b>Новый промокод</b>\n\nВыбери тип награды:", kb)
    await cb.answer()


@router.callback_query(F.data.startswith("admin:promo:k:"))
async def promo_kind(cb: CallbackQuery, state: FSMContext) -> None:
    kind = (cb.data or "").rsplit(":", 1)[-1]
    if kind not in _REWARDS:
        await cb.answer()
        return
    await state.set_state(PromoForm.value)
    await state.update_data(kind=kind)
    unit = "рублей" if kind == "balance" else "дней"
    await show_screen(
        cb,
        f"🎟 <b>{hesc(_REWARDS[kind][1])}</b>\n\n"
        f"Пришли: <b>значение</b> и (опц.) <b>лимит активаций</b> одним сообщением.\n"
        f"Напр. <code>{500 if kind == 'balance' else 30}</code> — {unit}, безлимит.\n"
        f"<code>{500 if kind == 'balance' else 30} 100</code> — с лимитом 100 активаций.",
        back_kb("admin:promo"),
    )
    await ack(cb)


@router.message(PromoForm.value, F.text)
async def promo_create(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    data = await state.get_data()
    kind = str(data.get("kind") or "balance")
    parts = (message.text or "").split()
    nums = parse_ints((message.text or ""), len(parts)) if parts else None
    if not nums or len(nums) not in (1, 2) or nums[0] <= 0 or (len(nums) == 2 and nums[1] <= 0):
        await message.answer(
            "Нужно число (и опционально лимит). Пример: <code>30</code> или <code>30 100</code>.",
            parse_mode="HTML",
        )
        return
    await state.clear()
    reward_type, _ = _REWARDS[kind]
    value = _reward_value(kind, nums[0])
    max_act = nums[1] if len(nums) == 2 else None
    code = _gen_code()
    async with container.uow() as uow:
        while await uow.promocodes.find_one(code=code) is not None:
            code = _gen_code()
        await uow.promocodes.add(
            Promocode(
                code=code,
                reward_type=reward_type,
                reward_value=value,
                max_activations=max_act,
            )
        )
        await uow.commit()
    cap = "∞" if max_act is None else str(max_act)
    await message.answer(
        f"✅ Промокод создан\n\n<code>{code}</code> — {_REWARDS[kind][1]} · лимит {cap}\n\n"
        f"Юзер вводит его на экране «Промокод».",
        parse_mode="HTML",
    )


# --- gift-code batches ---------------------------------------------------------


@router.callback_query(F.data == "admin:gift")
async def gift_home(cb: CallbackQuery) -> None:
    kb = rows_kb(
        [[(label, f"admin:gift:k:{k}")] for k, (_, label) in _REWARDS.items()]
        + [[("‹ Назад", MENU_CB)]]
    )
    await show_screen(
        cb,
        "🎁 <b>Gift-коды</b>\n\nОдноразовые коды с диплинком "
        "<code>?start=gift_КОД</code> — раздают в один тап.\n\nВыбери тип награды:",
        kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("admin:gift:k:"))
async def gift_kind(cb: CallbackQuery, state: FSMContext) -> None:
    kind = (cb.data or "").rsplit(":", 1)[-1]
    if kind not in _REWARDS:
        await cb.answer()
        return
    await state.set_state(GiftForm.value)
    await state.update_data(kind=kind)
    unit = "рублей" if kind == "balance" else "дней"
    await show_screen(
        cb,
        f"🎁 <b>{hesc(_REWARDS[kind][1])}</b>\n\n"
        f"Пришли: <b>значение</b> и <b>количество кодов</b> одним сообщением.\n"
        f"Напр. <code>{500 if kind == 'balance' else 30} 50</code> — "
        f"{unit}, 50 кодов (до 1000).",
        back_kb("admin:gift"),
    )
    await ack(cb)


@router.message(GiftForm.value, F.text)
async def gift_create(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    data = await state.get_data()
    kind = str(data.get("kind") or "days")
    nums = parse_ints((message.text or ""), 2)
    if not nums or nums[0] <= 0 or not (1 <= nums[1] <= 1000):
        await message.answer(
            "Нужно два числа: значение и количество (1..1000). Пример: <code>30 50</code>.",
            parse_mode="HTML",
        )
        return
    await state.clear()
    reward_type, _ = _REWARDS[kind]
    value = _reward_value(kind, nums[0])
    want = nums[1]
    codes: list[str] = []
    async with container.uow() as uow:
        bot_username = str(await container.bot_config.value(uow, "BOT_USERNAME") or "")
        for _ in range(want):
            for _attempt in range(6):
                code = f"GIFT-{_gen_code(8)}"
                if await uow.promocodes.find_one(code=code) is None:
                    break
            else:
                continue
            await uow.promocodes.add(
                Promocode(
                    code=code,
                    reward_type=reward_type,
                    reward_value=value,
                    max_activations=1,
                )
            )
            codes.append(code)
        await uow.commit()
    link = f"https://t.me/{bot_username}?start=gift_" if bot_username else ""
    body = "\n".join(f"{link}{c}" if link else c for c in codes)
    caption = f"✅ Готово: {len(codes)} gift-кодов ({_REWARDS[kind][1]})."
    if len(codes) <= 30:
        await message.answer(f"{caption}\n\n<code>{hesc(body)}</code>", parse_mode="HTML")
    else:
        doc = BufferedInputFile(body.encode("utf-8"), filename="gift-codes.txt")
        await message.answer_document(doc, caption=caption)


# --- sale campaigns ------------------------------------------------------------


@router.callback_query(F.data == "admin:sales")
async def sales_home(cb: CallbackQuery, container: AppContainer) -> None:
    async with container.uow() as uow:
        sales = list(await uow.sales.ordered())
    lines = ["🏷 <b>Акции</b>", "", "Скидка активна в выбранные дни месяца."]
    rows: list[list[tuple[str, str]]] = []
    if not sales:
        lines.append("\nПока нет. Нажми «Создать акцию».")
    for s in sales:
        flag = "✅" if s.enabled else "❌"
        lines.append(
            f"\n{flag} <b>−{s.discount_pct}%</b> · дни {s.start_day}–{s.end_day} · "
            f"исп. {s.used_count}/{'∞' if s.max_uses == 0 else s.max_uses}"
        )
        rows.append(
            [(f"{flag} −{s.discount_pct}% ({s.start_day}–{s.end_day})", f"admin:sale:t:{s.id}")]
        )
    rows.append([("➕ Создать акцию", "admin:sales:new")])
    rows.append([("‹ Назад", MENU_CB)])
    await show_screen(cb, "\n".join(lines), rows_kb(rows))
    await safe_answer(cb)  # sale_toggle chains here after already answering


@router.callback_query(F.data == "admin:sales:new")
async def sales_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SaleForm.value)
    await show_screen(
        cb,
        "🏷 <b>Новая акция</b>\n\nПришли три числа: <b>скидка% день_с день_по</b>.\n"
        "Напр. <code>20 1 3</code> — −20% с 1-го по 3-е число каждого месяца.",
        back_kb("admin:sales"),
    )
    await ack(cb)


@router.message(SaleForm.value, F.text)
async def sales_create(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    nums = parse_ints((message.text or ""), 3)
    if (
        not nums
        or not (1 <= nums[0] <= 100)
        or not (1 <= nums[1] <= 31)
        or not (nums[1] <= nums[2] <= 31)
    ):
        await message.answer(
            "Нужно: скидка (1..100), день_с (1..31), день_по (≥ день_с, ≤31). "
            "Пример: <code>20 1 3</code>.",
            parse_mode="HTML",
        )
        return
    await state.clear()
    pct, start_day, end_day = nums
    async with container.uow() as uow:
        await uow.sales.add(
            SaleCampaign(
                title=f"Скидка −{pct}%",
                discount_pct=pct,
                start_day=start_day,
                end_day=end_day,
                max_uses=0,
                enabled=True,
            )
        )
        await uow.commit()
    await message.answer(
        f"✅ Акция создана: −{pct}% с {start_day}-го по {end_day}-е число месяца.",
    )


@router.callback_query(F.data.startswith("admin:sale:t:"))
async def sale_toggle(cb: CallbackQuery, container: AppContainer) -> None:
    try:
        sale_id = int((cb.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await cb.answer()
        return
    async with container.uow() as uow:
        sale = await uow.sales.get(sale_id)
        if sale is None:
            await cb.answer("Акция удалена", show_alert=True)
            return
        sale.enabled = not sale.enabled
        await uow.commit()
    await cb.answer("Включена" if sale.enabled else "Выключена")
    await sales_home(cb, container)
