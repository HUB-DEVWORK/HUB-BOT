"""User management: find, card, balance +/-, extend subscription, block/unblock.

Same operations as the web cabinet (users.py): increment_balance + a GIFT/WITHDRAWAL
ledger row for money, subscriptions.renew for days, user.status for the block flag.
"""

from __future__ import annotations

from html import escape as hesc

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select

from src.bot.handlers.admin._common import MENU_CB, back_kb, rub
from src.bot.handlers.reply_menu import maybe_dispatch_menu_button
from src.bot.screen import ack, show_screen
from src.core.enums import TransactionStatus, TransactionType, UserStatus
from src.core.exceptions import DomainError
from src.infrastructure.database.base import utcnow
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.di import AppContainer

router = Router(name="admin-users")

# Match the web cabinet's BalanceIn bound (±10 000 000 ₽) so a fat-finger can't grant an
# absurd real-money balance or overflow the BigInt column.
MAX_BALANCE_RUB = 10_000_000


class FindForm(StatesGroup):
    query = State()


class BalanceForm(StatesGroup):
    amount = State()


def _is_int(s: str) -> bool:
    # str.isdigit() is True for unicode digits (e.g. '²') that int() rejects — restrict to ascii.
    return s.isascii() and s.isdigit()


def _like_escape(s: str) -> str:
    """Escape LIKE wildcards so a literal % or _ in the query isn't a wildcard match."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _find_user(uow: UnitOfWork, query: str) -> User | None:
    q = query.strip().lstrip("@")
    if not q:
        return None
    if _is_int(q):
        by_tg = await uow.users.get_by_telegram_id(int(q))
        if by_tg is not None:
            return by_tg
    needle = f"%{_like_escape(q.lower())}%"
    stmt = (
        select(User)
        .where(
            or_(
                func.lower(User.username).like(needle, escape="\\"),
                func.lower(User.first_name).like(needle, escape="\\"),
            )
        )
        .limit(1)
    )
    return (await uow.session.execute(stmt)).scalar_one_or_none()


def _card_kb(user: User) -> InlineKeyboardMarkup:
    uid = user.id
    blocked = user.status is UserStatus.BLOCKED
    rows = [
        [
            InlineKeyboardButton(text="➕ Баланс", callback_data=f"admin:u:bal:add:{uid}"),
            InlineKeyboardButton(text="➖ Баланс", callback_data=f"admin:u:bal:sub:{uid}"),
        ],
        [
            InlineKeyboardButton(text="+7 дн", callback_data=f"admin:u:ext:{uid}:7"),
            InlineKeyboardButton(text="+30 дн", callback_data=f"admin:u:ext:{uid}:30"),
            InlineKeyboardButton(text="+90 дн", callback_data=f"admin:u:ext:{uid}:90"),
        ],
        [
            InlineKeyboardButton(
                text="🔓 Разбанить" if blocked else "🚫 Забанить",
                callback_data=f"admin:u:ban:{uid}",
            )
        ],
        [InlineKeyboardButton(text="‹ Назад", callback_data="admin:users")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_card(target: CallbackQuery | Message, container: AppContainer, uid: int) -> None:
    async with container.uow() as uow:
        user = await uow.users.get(uid)
        if user is None:
            await show_screen(target, "Пользователь не найден.", back_kb("admin:users"))
            return
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
        invited = await uow.users.count(referred_by_id=user.id)
    handle = f"@{user.username}" if user.username else (user.first_name or "—")
    status = "🚫 забанен" if user.status is UserStatus.BLOCKED else "активен"
    lines = [
        f"👤 <b>{hesc(handle)}</b>",
        f"ID {user.id} · TG <code>{user.telegram_id}</code> · {status}",
        f"💰 Баланс: <b>{rub(user.balance_minor)}</b>",
        f"🤝 Приглашено: {invited}",
    ]
    if sub is not None:
        expire = sub.expire_at.strftime("%d.%m.%Y") if sub.expire_at else "—"
        lines.append(f"📶 Подписка: {sub.status.value} · до {expire}")
    else:
        lines.append("📶 Подписки нет")
    await show_screen(target, "\n".join(lines), _card_kb(user))


@router.callback_query(F.data == "admin:users")
async def users_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FindForm.query)
    await show_screen(
        cb,
        "👥 <b>Пользователи</b>\n\nПришли <b>Telegram ID</b>, <b>@username</b> или имя — "
        "найду карточку.",
        back_kb(MENU_CB),
    )
    await ack(cb)


@router.message(FindForm.query, F.text)
async def users_find(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    await state.clear()
    async with container.uow() as uow:
        user = await _find_user(uow, message.text or "")
        uid = user.id if user else None
    if uid is None:
        await message.answer("Не нашёл такого пользователя. Попробуй ещё раз через 👥.")
        return
    await _render_card(message, container, uid)


@router.callback_query(F.data.startswith("admin:u:card:"))
async def user_card(cb: CallbackQuery, container: AppContainer) -> None:
    try:
        uid = int((cb.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await cb.answer()
        return
    await _render_card(cb, container, uid)
    await cb.answer()


@router.callback_query(F.data.startswith("admin:u:bal:"))
async def balance_ask(cb: CallbackQuery, state: FSMContext) -> None:
    # admin:u:bal:<add|sub>:<uid>
    parts = (cb.data or "").split(":")
    if len(parts) != 5 or parts[3] not in ("add", "sub"):
        await cb.answer()
        return
    sign, uid_raw = parts[3], parts[4]
    try:
        uid = int(uid_raw)
    except ValueError:
        await cb.answer()
        return
    await state.set_state(BalanceForm.amount)
    await state.update_data(uid=uid, sign=sign)
    verb = "начислить" if sign == "add" else "списать"
    await show_screen(
        cb,
        f"💰 Сколько рублей {verb}? Пришли число, напр. <code>500</code>.",
        back_kb(f"admin:u:card:{uid}"),
    )
    await ack(cb)


@router.message(BalanceForm.amount, F.text)
async def balance_apply(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    if await maybe_dispatch_menu_button(message, container, db_user, state):
        return
    data = await state.get_data()
    uid = int(data.get("uid") or 0)
    sign = str(data.get("sign") or "add")
    raw = (message.text or "").strip()
    if not _is_int(raw) or not (0 < int(raw) <= MAX_BALANCE_RUB):
        await message.answer(
            f"Нужно число рублей от 1 до {MAX_BALANCE_RUB:,}".replace(",", " ")
            + ", напр. <code>500</code>.",
            parse_mode="HTML",
        )
        return
    await state.clear()
    delta = int(raw) * 100 * (1 if sign == "add" else -1)
    async with container.uow() as uow:
        user = await uow.users.get(uid)
        if user is None:
            await message.answer("Пользователь не найден.")
            return
        await uow.users.increment_balance(user, delta)
        await uow.transactions.add(
            Transaction(
                user_id=user.id,
                type=TransactionType.GIFT if delta > 0 else TransactionType.WITHDRAWAL,
                status=TransactionStatus.COMPLETED,
                amount_minor=abs(delta),
                currency=user.currency,
                payment_method="admin",
                gateway_display_name="admin (бот)",
                completed_at=utcnow(),
            )
        )
        await uow.commit()
    await message.answer(f"✅ Баланс изменён на {rub(delta)}. Текущий: {rub(user.balance_minor)}.")
    await _render_card(message, container, uid)


@router.callback_query(F.data.startswith("admin:u:ext:"))
async def user_extend(cb: CallbackQuery, container: AppContainer) -> None:
    # admin:u:ext:<uid>:<days>
    parts = (cb.data or "").split(":")
    try:
        uid, days = int(parts[3]), int(parts[4])
    except (IndexError, ValueError):
        await cb.answer()
        return
    async with container.uow() as uow:
        user = await uow.users.get(uid)
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user and user.current_subscription_id
            else None
        )
        if user is None or sub is None:
            await cb.answer("Нет активной подписки — продлевать нечего", show_alert=True)
            return
        try:
            await container.subscriptions.renew(uow, sub, days=days, telegram_id=user.telegram_id)
        except DomainError:
            # RemnawaveError (panel down) or PurchaseError (sub in a state renew rejects).
            await cb.answer(
                "Не удалось продлить — панель недоступна или подписка неактивна", show_alert=True
            )
            return
        await uow.commit()
    await cb.answer(f"+{days} дней выдано")
    await _render_card(cb, container, uid)


@router.callback_query(F.data.startswith("admin:u:ban:"))
async def user_ban(cb: CallbackQuery, container: AppContainer) -> None:
    try:
        uid = int((cb.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await cb.answer()
        return
    async with container.uow() as uow:
        user = await uow.users.get(uid)
        if user is None:
            await cb.answer("Пользователь не найден", show_alert=True)
            return
        if user.role.is_staff and user.status is not UserStatus.BLOCKED:
            await cb.answer("Нельзя забанить администратора", show_alert=True)
            return
        user.status = UserStatus.ACTIVE if user.status is UserStatus.BLOCKED else UserStatus.BLOCKED
        await uow.commit()
    await cb.answer("Разбанен" if user.status is UserStatus.ACTIVE else "Забанен")
    await _render_card(cb, container, uid)
