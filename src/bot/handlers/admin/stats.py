"""Stats & analytics screens (read-only)."""

from __future__ import annotations

from html import escape as hesc

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from src.bot.handlers.admin._common import back_kb, rub
from src.bot.screen import show_screen
from src.core.enums import TransactionStatus, TransactionType
from src.infrastructure.database.models.plan import Plan
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.di import AppContainer

router = Router(name="admin-stats")


async def _top_gateways(uow: UnitOfWork, limit: int = 5) -> list[tuple[str, int]]:
    stmt = (
        select(
            func.coalesce(Transaction.gateway_display_name, Transaction.payment_method, "—").label(
                "src"
            ),
            func.sum(Transaction.amount_minor),
        )
        .where(
            Transaction.status == TransactionStatus.COMPLETED,
            Transaction.type == TransactionType.DEPOSIT,
            Transaction.gateway_type.is_not(None),
        )
        .group_by("src")
        .order_by(func.sum(Transaction.amount_minor).desc())
        .limit(limit)
    )
    return [(str(name), int(total)) for name, total in (await uow.session.execute(stmt)).all()]


async def _top_plans(uow: UnitOfWork, limit: int = 5) -> list[tuple[str, int]]:
    stmt = (
        select(Plan.name, func.count(Subscription.id))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .group_by(Plan.name)
        .order_by(func.count(Subscription.id).desc())
        .limit(limit)
    )
    return [(str(name), int(n)) for name, n in (await uow.session.execute(stmt)).all()]


@router.callback_query(F.data == "admin:stats")
async def admin_stats(cb: CallbackQuery, container: AppContainer) -> None:
    """Revenue by period + funnel snapshot + top gateways/plans, one screen."""
    from src.infrastructure.services import analytics as svc

    async with container.uow() as uow:
        o = await svc.overview(uow)
        gateways = await _top_gateways(uow)
        plans = await _top_plans(uow)

    lines = [
        "📊 <b>Статистика</b>",
        "",
        f"👥 Пользователей: <b>{o['users']}</b> "
        f"(+{o['new_today']} сегодня · +{o['new_week']} за 7д · +{o['new_month']} за 30д)",
        f"✅ Активных подписок: <b>{o['active_subscriptions']}</b> · "
        f"на триале: <b>{o['on_trial']}</b>",
        "",
        "💰 <b>Выручка</b>",
        f"├ Сегодня: <b>{rub(o['revenue_today_minor'])}</b>",
        f"├ 7 дней: <b>{rub(o['revenue_week_minor'])}</b>",
        f"├ 30 дней: <b>{rub(o['revenue_month_minor'])}</b>",
        f"└ Всего: <b>{rub(o['revenue_minor'])}</b>",
    ]
    if plans:
        lines += ["", "🏆 <b>Топ тарифов</b> (по подпискам)"]
        lines += [f"• {hesc(name)}: {n}" for name, n in plans]
    if gateways:
        lines += ["", "💳 <b>Топ касс</b> (пополнения)"]
        lines += [f"• {hesc(name)}: {rub(total)}" for name, total in gateways]
    await show_screen(cb, "\n".join(lines), back_kb())
    await cb.answer()


@router.callback_query(F.data == "admin:analytics")
async def admin_analytics(cb: CallbackQuery, container: AppContainer) -> None:
    """Funnel + ARPU + retention + acquisition sources."""
    from src.infrastructure.services import analytics as svc

    async with container.uow() as uow:
        data = await svc.full(uow)
    o, r, s = data["overview"], data["retention"], data["sources"]
    lines = [
        "📈 <b>Аналитика</b>",
        "",
        "🎯 <b>Воронка</b>",
        f"├ Триал: <b>{o['ever_trial']}</b> → оплатили: <b>{o['paid_users']}</b> "
        f"({o['trial_to_paid_pct']}%)",
        f"└ Конверсия в оплату: <b>{o['conversion_paid_pct']}%</b>",
        "",
        "💵 <b>Средние</b>",
        f"ARPU {rub(o['arpu_minor'])} · ARPPU {rub(o['arppu_minor'])} · "
        f"чек {rub(o['avg_check_minor'])}",
        "",
        f"🔁 <b>Удержание</b>: 7д {r['d7']['pct']}% ({r['d7']['retained']}/{r['d7']['cohort']}) · "
        f"30д {r['d30']['pct']}% ({r['d30']['retained']}/{r['d30']['cohort']})",
    ]
    if s["campaigns"]:
        lines += ["", "🚀 <b>Источники (ТОП)</b>"]
        lines += [
            f"• {hesc(c['name'])}: {c['users']} юз · {rub(c['revenue_minor'])} · "
            f"ROI {rub(c['roi_minor'])}"
            for c in s["campaigns"]
        ]
    if s["referrers"]:
        lines += ["", "🤝 <b>ТОП рефереров</b>"]
        lines += [f"• {hesc(ref['label'])}: {ref['invited']}" for ref in s["referrers"]]
    await show_screen(cb, "\n".join(lines), back_kb())
    await cb.answer()
