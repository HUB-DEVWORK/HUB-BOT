"""Background tasks. Import path registered with the worker (see compose.local.yml)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING
from uuid import UUID

from src.core.enums import TransactionStatus, TransactionType
from src.core.logging import get_logger
from src.infrastructure.taskiq.broker import broker, get_container

if TYPE_CHECKING:
    from src.infrastructure.di import AppContainer

log = get_logger(__name__)


@broker.task(retry_on_error=True, max_retries=5)
async def process_payment(
    payment_id: str,
    status: str,
    saved_method_enc: str | None = None,
    saved_method_title: str | None = None,
) -> bool:
    """Complete a transaction from a verified webhook (idempotent CAS + fulfilment).

    Enqueued by the payment webhook route; never run inline. Safe to retry — a duplicate
    finds the transaction already terminal and no-ops. Notifies the buyer AFTER commit (only
    the out-of-band webhook path reaches here; in-bot flows reply in the handler themselves).
    ``saved_method_enc`` is a provider-saved card token (Fernet-encrypted by the route) —
    persisted on the paying user for card autopay.
    """
    container = get_container()
    pid = UUID(payment_id)
    async with container.uow() as uow:
        moved = await container.payments.process(
            uow, payment_id=pid, status=TransactionStatus(status)
        )
        await uow.commit()
    log.info("process_payment", payment_id=payment_id, status=status, advanced=moved)

    if saved_method_enc:  # a duplicate webhook still carries a valid token; store is idempotent
        await _store_saved_method(
            container, pid, method_enc=saved_method_enc, title=saved_method_title
        )
    if moved and status == TransactionStatus.COMPLETED.value:
        await _notify_paid(container, pid)
    return moved


async def _store_saved_method(
    container: object, payment_id: UUID, *, method_enc: str, title: str | None
) -> None:
    """Persist a provider-saved card on the transaction's user (token stays encrypted)."""
    from typing import cast

    from src.infrastructure.di import AppContainer

    c = cast(AppContainer, container)
    async with c.uow() as uow:
        txn = await uow.transactions.get_by_payment_id(payment_id)
        if txn is None:
            return
        user = await uow.users.get(txn.user_id)
        if user is None:
            return
        user.saved_payment_method_id = method_enc
        user.saved_payment_method_title = title
        await uow.commit()
        log.info("saved payment method stored", user_id=user.id, title=title)


async def _notify_paid(container: object, payment_id: UUID) -> None:
    """DM the buyer that an out-of-band payment completed. Best-effort, post-commit."""
    from typing import cast

    from src.infrastructure.di import AppContainer

    c = cast(AppContainer, container)
    async with c.uow() as uow:
        txn = await uow.transactions.get_by_payment_id(payment_id)
        if txn is None:
            return
        user = await uow.users.get(txn.user_id)
        if user is None or user.telegram_id is None:
            return
        if txn.type is TransactionType.DEPOSIT:
            text = "✅ Баланс пополнен."
        elif txn.type is TransactionType.SUBSCRIPTION_PAYMENT:
            text = "✅ Оплата получена — подписка активна!"
            if user.current_subscription_id is not None:
                sub = await uow.subscriptions.get(user.current_subscription_id)
                if sub is not None and sub.subscription_url:
                    text += f"\n{sub.subscription_url}"
        else:
            text = "✅ Оплата получена."
        telegram_id = user.telegram_id
    await c.notifier.notify_user(telegram_id, text)


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def reconcile_pending_payments() -> int:
    """Recover paid-but-stuck gateway transactions by polling the provider.

    Covers both real-world failure modes of webhook delivery: the webhook never
    arrived (proxy/CDN ate it, wrong URL) and the fulfilment crashed after we had
    already answered 200 (panel outage). Polling is idempotent — PaymentService
    CAS-guards the transition, so a race with a late webhook is harmless.
    """
    import datetime as dt

    from src.infrastructure.payments.crypto import decrypt_gateway_settings

    container = get_container()
    now = dt.datetime.now(dt.UTC)
    recovered = 0
    async with container.uow() as uow:
        stuck = await uow.transactions.list_stuck_pending(
            older_than=now - dt.timedelta(minutes=3),
            newer_than=now - dt.timedelta(hours=24),
        )
        rows = {g.type: g for g in await uow.payment_gateways.list() if g.is_active}
    for txn in stuck:
        gtype = txn.gateway_type
        if gtype is None:  # filtered by the query, but keep mypy honest
            continue
        row = rows.get(gtype)
        if row is None or gtype not in container.gateway_factory.supported():
            continue
        gateway = container.gateway_factory.create(
            gtype, decrypt_gateway_settings(container.secret_box, dict(row.settings))
        )
        try:
            result = await gateway.fetch_status(str(txn.external_id))
        except Exception as exc:
            log.warning("reconcile poll failed", payment_id=str(txn.payment_id), error=str(exc))
            continue
        if result is None or result.status is TransactionStatus.PENDING:
            continue
        if result.saved_method is not None:  # webhook was lost — don't lose the card with it
            enc = (
                container.secret_box.encrypt(result.saved_method.method_id)
                if container.secret_box
                else result.saved_method.method_id
            )
            await _store_saved_method(
                container, txn.payment_id, method_enc=enc, title=result.saved_method.title
            )
        try:
            async with container.uow() as uow:
                moved = await container.payments.process(
                    uow, payment_id=txn.payment_id, status=result.status
                )
                await uow.commit()
        except Exception as exc:
            log.warning("reconcile process failed", payment_id=str(txn.payment_id), error=str(exc))
            continue
        if moved:
            recovered += 1
            log.info(
                "payment reconciled",
                payment_id=str(txn.payment_id),
                status=result.status.value,
            )
            if result.status is TransactionStatus.COMPLETED:
                await _notify_paid(container, txn.payment_id)
    return recovered


@broker.task
async def panel_write_retry(subscription_id: int) -> None:
    """Re-drive a failed panel write for a subscription (ADR-0005 retry queue).

    Placeholder for the reconcile/sync implementation — wire to RemnawaveService.apply once
    the sync mapper lands. Kept idempotent by design.
    """
    log.info("panel_write_retry", subscription_id=subscription_id)


@broker.task(schedule=[{"cron": "*/15 * * * *"}])
async def sync_panel_nodes() -> int:
    """Mirror Remnawave nodes into server_nodes (screen 12 + dashboard online)."""
    container = get_container()
    async with container.uow() as uow:
        try:
            synced = await container.panel_sync.sync_nodes(uow)
        except Exception as exc:
            log.warning("panel nodes sync failed", error=str(exc))
            return -1
        await uow.commit()
    log.info("panel nodes synced", count=synced)
    return synced


def _msk_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(hours=3)


def _time_matches(target_hhmm: str, now: dt.datetime, window_minutes: int = 5) -> bool:
    """True when `now` (MSK) is within [target, target+window)."""
    try:
        hh, mm = (int(x) for x in target_hhmm.split(":"))
    except ValueError:
        return False
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target <= now < target + dt.timedelta(minutes=window_minutes)


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def send_smart_reminders() -> int:
    """Renewal reminders: users whose subscription expires in N days (config days CSV).

    Runs every 5 minutes; fires only when MSK time enters the configured window.
    Per-user daily dedup via Redis SETNX.
    """
    from aiogram import Bot
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    from sqlalchemy import select

    from src.core.enums import SubscriptionStatus
    from src.infrastructure.database.models.subscription import Subscription
    from src.infrastructure.database.models.user import User

    container = get_container()
    now_msk = _msk_now()
    async with container.uow() as uow:
        reminder = await uow.smart_reminder.get_or_create()
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        await uow.commit()
        if not reminder.enabled or not _time_matches(reminder.send_time, now_msk):
            return 0
        day_offsets = [int(x) for x in reminder.days_before.split(",") if x.strip().isdigit()]
        if not day_offsets:
            return 0

        targets: list[tuple[int, int]] = []  # (telegram_id, days_left)
        for offset in day_offsets:
            day_start = (now_msk + dt.timedelta(days=offset)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - dt.timedelta(hours=3)  # back to UTC
            day_end = day_start + dt.timedelta(days=1)
            rows = (
                await uow.session.execute(
                    select(User.telegram_id)
                    .join(Subscription, Subscription.id == User.current_subscription_id)
                    .where(
                        User.telegram_id.is_not(None),
                        Subscription.status.in_(
                            [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL]
                        ),
                        Subscription.expire_at >= day_start,
                        Subscription.expire_at < day_end,
                    )
                )
            ).all()
            targets.extend((int(tg), offset) for (tg,) in rows)

    if not targets:
        return 0
    markup = None
    if reminder.button_enabled and miniapp_url.startswith("https://"):
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Продлить", web_app=WebAppInfo(url=miniapp_url))]
            ]
        )
    sent = 0
    today = now_msk.strftime("%Y%m%d")
    bot = Bot(token=container.settings.bot.token)
    try:
        for tg_id, days in targets:
            if not await container.redis.set(f"reminder:{today}:{tg_id}", "1", nx=True, ex=86400):
                continue  # already reminded today
            try:
                await bot.send_message(
                    tg_id, reminder.text.replace("{days}", str(days)), reply_markup=markup
                )
                sent += 1
            except Exception:
                pass
    finally:
        await bot.session.close()
    log.info("smart reminders sent", count=sent)
    return sent


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def send_winback_offers() -> int:
    """Win-back funnel (screen 08): N days after a subscription expired, message the user
    and grant a one-shot purchase discount (consumed by PricingService on the next buy).

    Runs every 5 minutes; an enabled step fires when MSK time enters its window and
    targets users whose current subscription expired exactly ``offset_days`` MSK-days
    ago — so each user walks the funnel rung by rung. Per-user daily dedup via Redis
    SETNX, mirroring send_smart_reminders.
    """
    from aiogram import Bot
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    from sqlalchemy import select

    from src.core.enums import SubscriptionStatus, UserStatus
    from src.infrastructure.database.models.subscription import Subscription
    from src.infrastructure.database.models.user import User
    from src.infrastructure.database.models.winback_step import WinbackStep

    container = get_container()
    now_msk = _msk_now()
    async with container.uow() as uow:
        steps = [
            s
            for s in await uow.winback_steps.ordered()
            if s.enabled and _time_matches(s.send_time, now_msk)
        ]
        if not steps:
            return 0
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")

        targets: list[tuple[int, int, WinbackStep]] = []  # (user_id, telegram_id, step)
        for step in steps:
            day_start = (now_msk - dt.timedelta(days=step.offset_days)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - dt.timedelta(hours=3)  # back to UTC
            day_end = day_start + dt.timedelta(days=1)
            rows = (
                await uow.session.execute(
                    select(User.id, User.telegram_id)
                    .join(Subscription, Subscription.id == User.current_subscription_id)
                    .where(
                        User.telegram_id.is_not(None),
                        User.status == UserStatus.ACTIVE,
                        Subscription.status == SubscriptionStatus.EXPIRED,
                        Subscription.expire_at >= day_start,
                        Subscription.expire_at < day_end,
                    )
                )
            ).all()
            targets.extend((int(uid), int(tg), step) for uid, tg in rows)

    if not targets:
        return 0
    markup = None
    if miniapp_url.startswith("https://"):
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Вернуться", web_app=WebAppInfo(url=miniapp_url))]
            ]
        )
    sent = 0
    today = now_msk.strftime("%Y%m%d")
    bot = Bot(token=container.settings.bot.token)
    try:
        for user_id, tg_id, step in targets:
            if not await container.redis.set(f"winback:{today}:{tg_id}", "1", nx=True, ex=86400):
                continue  # one win-back message per user per day
            if step.discount_pct > 0:
                async with container.uow() as uow:
                    user = await uow.users.get(user_id)
                    if user is None:
                        continue
                    # max, not assign: don't clobber a bigger one-shot promo discount
                    user.purchase_discount_pct = max(user.purchase_discount_pct, step.discount_pct)
                    await uow.commit()
            try:
                await bot.send_message(
                    tg_id,
                    step.text.replace("{discount}", str(step.discount_pct)),
                    reply_markup=markup,
                )
                sent += 1
            except Exception:
                pass
    finally:
        await bot.session.close()
    log.info("winback offers sent", count=sent)
    return sent


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def send_holiday_promos() -> int:
    """Holiday-calendar promos (screen 08): on the day, at the configured time."""
    from aiogram import Bot
    from sqlalchemy import select

    from src.core.enums import UserStatus
    from src.infrastructure.database.models.user import User

    container = get_container()
    now_msk = _msk_now()
    async with container.uow() as uow:
        holidays = [
            h
            for h in await uow.holidays.ordered()
            if h.enabled
            and h.month == now_msk.month
            and h.day == now_msk.day
            and _time_matches(h.send_time, now_msk)
        ]
        if not holidays:
            return 0
        rows = (
            await uow.session.execute(
                select(User.telegram_id).where(
                    User.telegram_id.is_not(None), User.status == UserStatus.ACTIVE
                )
            )
        ).all()
        chat_ids = [int(tg) for (tg,) in rows]

    sent_total = 0
    bot = Bot(token=container.settings.bot.token)
    try:
        for h in holidays:
            key = f"holiday:{now_msk.year}:{h.id}"
            if not await container.redis.set(key, "1", nx=True, ex=86400 * 2):
                continue  # already sent this year
            if h.reward_type.value == "discount":
                text = f"🎉 {h.name}! Скидка -{h.value}% на продление — только сегодня!"
            elif h.reward_type.value == "days":
                text = f"🎉 {h.name}! Дарим +{h.value} дней при продлении сегодня!"
            else:
                text = f"🎉 {h.name}! Бонус {h.value / 100:.0f} ₽ на баланс за продление сегодня!"
            sent = 0
            for chat_id in chat_ids:
                try:
                    await bot.send_message(chat_id, text)
                    sent += 1
                except Exception:
                    pass
                import asyncio

                await asyncio.sleep(0.04)
            sent_total += sent
            async with container.uow() as uow:
                row = await uow.holidays.get(h.id)
                if row is not None:
                    results = dict(row.results)
                    results[str(now_msk.year)] = {"sent": sent}
                    row.results = results
                    await uow.commit()
    finally:
        await bot.session.close()
    log.info("holiday promos sent", count=sent_total)
    return sent_total


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def scheduled_backup() -> None:
    """Fires run_backup at the configured BACKUP_TIME (daily, Redis dedup)."""
    container = get_container()
    now_msk = _msk_now()
    async with container.uow() as uow:
        enabled = bool(await container.bot_config.value(uow, "BACKUP_ENABLED"))
        at = str(await container.bot_config.value(uow, "BACKUP_TIME"))
    if not enabled or not _time_matches(at, now_msk):
        return
    if not await container.redis.set(
        f"backup:{now_msk.strftime('%Y%m%d')}", "1", nx=True, ex=86400
    ):
        return
    await run_backup.kiq()


@broker.task
async def run_backup() -> str | None:
    """Dump the Postgres DB to an encrypted zip in ./backups (pg_dump + pyzipper).

    Returns the archive path, or None when pg_dump is unavailable/failed. When the
    «backups» report topic is enabled, the archive is delivered into the report group.
    """
    import asyncio
    from pathlib import Path

    from src.infrastructure.services.backup import create_encrypted_zip, prune_old_backups

    container = get_container()
    db = container.settings.database
    backups = Path("backups")
    backups.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")
    dump_path = backups / f"db_{stamp}.sql"

    proc = await asyncio.create_subprocess_exec(
        "pg_dump",
        "--no-owner",
        "--format=plain",
        f"--file={dump_path}",
        f"--dbname=postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("run_backup pg_dump failed", stderr=stderr.decode()[:400])
        dump_path.unlink(missing_ok=True)
        return None

    async with container.uow() as uow:
        password = str(await container.bot_config.value(uow, "BACKUP_ENCRYPTION_PASSWORD") or "")
        keep = int(await container.bot_config.value(uow, "BACKUP_KEEP_LAST"))
    out = backups / f"backup_{stamp}.zip"
    create_encrypted_zip([dump_path], out, password or container.settings.app.jwt_secret[:16])
    dump_path.unlink(missing_ok=True)
    prune_old_backups(backups, keep)
    log.info("run_backup done", path=str(out))

    from src.infrastructure.services.reports import send_topic_report

    await send_topic_report(container, "backups", f"💾 Бэкап БД · {stamp}", document=out)
    return str(out)


_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_WEEKDAYS |= {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}  # noqa: RUF001


def _parse_weekly_schedule(raw: str) -> tuple[int | None, str]:
    """'Mon 10:00' → (0, '10:00'); bare 'HH:MM' → (None, 'HH:MM') = any day."""
    parts = raw.strip().split()
    if len(parts) == 2 and parts[0].lower()[:3] in _WEEKDAYS:
        return _WEEKDAYS[parts[0].lower()[:3]], parts[1]
    return None, parts[-1] if parts else ""


async def _summary_text(container: AppContainer, title: str, *, hours: int) -> str:
    """KPI block over the trailing window. Revenue definition mirrors the dashboard:
    external money only — balance-funded purchases are internal transfers."""
    from sqlalchemy import func, select

    from src.core.enums import SubscriptionStatus
    from src.infrastructure.database.models.subscription import Subscription
    from src.infrastructure.database.models.ticket import Ticket
    from src.infrastructure.database.models.transaction import Transaction
    from src.infrastructure.database.models.user import User
    from src.infrastructure.services.reports import fmt_amount
    from src.web.routes.admin.dashboard import _EXTERNAL_MONEY

    start = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    paid = (
        Transaction.status == TransactionStatus.COMPLETED,
        _EXTERNAL_MONEY,
        Transaction.completed_at >= start,
        Transaction.is_test.is_(False),
    )
    async with container.uow() as uow:
        s = uow.session
        new_users = int(
            await s.scalar(select(func.count()).select_from(User).where(User.created_at >= start))
            or 0
        )
        trials = int(
            await s.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.is_trial.is_(True), Subscription.created_at >= start)
            )
            or 0
        )
        payments = int(
            await s.scalar(select(func.count()).select_from(Transaction).where(*paid)) or 0
        )
        revenue = int(
            await s.scalar(
                select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(*paid)
            )
            or 0
        )
        active = int(
            await s.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.status.in_(
                        [
                            SubscriptionStatus.ACTIVE,
                            SubscriptionStatus.TRIAL,
                            SubscriptionStatus.LIMITED,
                        ]
                    )
                )
            )
            or 0
        )
        tickets = int(
            await s.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.created_at >= start)
            )
            or 0
        )
    return (
        f"<b>{title}</b>\n\n"
        f"👥 Новых пользователей: <b>{new_users}</b>\n"
        f"🧪 Триалов выдано: <b>{trials}</b>\n"
        f"💳 Платежей: <b>{payments}</b> на <b>{fmt_amount(revenue)}</b>\n"
        f"📦 Активных подписок: <b>{active}</b>\n"
        f"🎫 Тикетов открыто: <b>{tickets}</b>"
    )


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def send_periodic_reports() -> int:
    """Daily/weekly summaries into the report group topics (screen 14).

    Runs every 5 minutes; an enabled topic fires when MSK time enters its schedule
    window ("21:00" / "Mon 10:00" from report_topics.schedule), once per period
    (Redis SETNX dedup). Instant topics are event-driven — see services/reports.py.
    """
    from src.infrastructure.services.reports import send_topic_report

    container = get_container()
    now_msk = _msk_now()
    async with container.uow() as uow:
        topics = {t.code: (t.enabled, t.schedule) for t in await uow.report_topics.list()}

    sent = 0
    enabled, schedule = topics.get("daily_report", (False, None))
    if (
        enabled
        and _time_matches(schedule or "21:00", now_msk)
        and await container.redis.set(f"report:daily:{now_msk:%Y%m%d}", "1", nx=True, ex=86400)
    ):
        text = await _summary_text(container, f"📊 Отчёт за сутки · {now_msk:%d.%m.%Y}", hours=24)
        if await send_topic_report(container, "daily_report", text):
            sent += 1

    enabled, schedule = topics.get("weekly_report", (False, None))
    if enabled:
        day, hhmm = _parse_weekly_schedule(schedule or "Mon 10:00")
        if (day is None or now_msk.weekday() == day) and _time_matches(hhmm, now_msk):
            iso = now_msk.isocalendar()
            key = f"report:weekly:{iso.year}{iso.week:02d}"
            if await container.redis.set(key, "1", nx=True, ex=86400 * 8):
                text = await _summary_text(
                    container, f"📈 Отчёт за неделю · {now_msk:%d.%m.%Y}", hours=24 * 7
                )
                if await send_topic_report(container, "weekly_report", text):
                    sent += 1
    if sent:
        log.info("periodic reports sent", count=sent)
    return sent


@broker.task
async def send_broadcast(broadcast_id: int) -> None:
    """Deliver a broadcast to its audience, updating live progress counters.

    Uses a bare aiogram ``Bot`` (no dispatcher) so the worker doesn't need the bot
    process. Progress is committed in batches so the cabinet's polling sees it grow;
    per-user failures (blocked bot, deactivated account) increment ``failed``.
    """
    import asyncio
    from pathlib import Path

    from aiogram import Bot
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
    from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

    from src.bot.keyboards import style_for_hex
    from src.core.enums import BroadcastMedia, BroadcastStatus
    from src.web.routes.admin.broadcasts import audience_stmt

    container = get_container()
    async with container.uow() as uow:
        b = await uow.broadcasts.get(broadcast_id)
        if b is None or b.status not in (BroadcastStatus.PENDING, BroadcastStatus.RUNNING):
            return
        b.status = BroadcastStatus.RUNNING
        b.started_at = dt.datetime.now(dt.UTC)
        chat_ids = [
            int(tg) for (tg,) in (await uow.session.execute(audience_stmt(b.audience))).all()
        ]
        text, button_enabled = b.text, b.button_enabled
        button_text, button_url = b.button_text, b.button_url
        button_action, button_color = b.button_action, b.button_color
        media, media_path, emoji_id = b.media, b.media_path, b.emoji_id
        await uow.commit()

    # Premium custom emoji renders via <tg-emoji>; falls back to the plain glyph.
    if emoji_id:
        text = f'<tg-emoji emoji-id="{emoji_id}">⭐</tg-emoji> {text}'

    markup = None
    if button_enabled and button_text and (button_url or button_action):
        kwargs: dict[str, object] = {"text": button_text}
        if button_url:
            kwargs["url"] = button_url
        else:
            kwargs["callback_data"] = f"act:{button_action}:0"
        style = style_for_hex(button_color)
        if style:
            kwargs["style"] = style
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(**kwargs)]]  # type: ignore[arg-type]
        )

    media_file = None
    if media is not BroadcastMedia.TEXT and media_path and Path(media_path).is_file():
        media_file = FSInputFile(media_path)

    async def deliver(chat_id: int) -> None:
        if media_file is not None and media is BroadcastMedia.PHOTO:
            await bot.send_photo(
                chat_id, media_file, caption=text, reply_markup=markup, parse_mode="HTML"
            )
        elif media_file is not None and media is BroadcastMedia.VIDEO:
            await bot.send_video(
                chat_id, media_file, caption=text, reply_markup=markup, parse_mode="HTML"
            )
        elif media_file is not None and media is BroadcastMedia.GIF:
            await bot.send_animation(
                chat_id, media_file, caption=text, reply_markup=markup, parse_mode="HTML"
            )
        else:
            await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

    sent = failed = 0
    bot = Bot(token=container.settings.bot.token)
    try:
        for i, chat_id in enumerate(chat_ids, start=1):
            try:
                await deliver(chat_id)
                sent += 1
            except TelegramRetryAfter as exc:  # flood control — wait and retry once
                await asyncio.sleep(exc.retry_after)
                try:
                    await deliver(chat_id)
                    sent += 1
                except Exception:
                    failed += 1
            except TelegramForbiddenError:
                failed += 1
            except Exception:
                failed += 1
            # ~28 msg/s ceiling keeps us under Telegram's 30/s global limit.
            await asyncio.sleep(0.036)
            if i % 25 == 0 or i == len(chat_ids):
                async with container.uow() as uow:
                    b = await uow.broadcasts.get(broadcast_id)
                    if b is None or b.status is BroadcastStatus.CANCELED:
                        return
                    b.sent, b.failed = sent, failed
                    await uow.commit()
    finally:
        await bot.session.close()

    async with container.uow() as uow:
        b = await uow.broadcasts.get(broadcast_id)
        if b is not None:
            b.sent, b.failed = sent, failed
            b.status = BroadcastStatus.DONE
            b.finished_at = dt.datetime.now(dt.UTC)
            await uow.commit()
    log.info("send_broadcast done", broadcast_id=broadcast_id, sent=sent, failed=failed)


@broker.task(schedule=[{"cron": "23 */2 * * *"}])
async def process_autopay() -> int:
    """Auto-renew from balance (#2): charge the wallet and extend subscriptions that are about
    to expire and have autopay enabled. Idempotent — a renewed sub leaves the window."""
    from sqlalchemy import select

    from src.core.enums import SubscriptionStatus
    from src.infrastructure.database.base import utcnow
    from src.infrastructure.database.models.subscription import Subscription

    container = get_container()
    async with container.uow() as uow:
        if not bool(await container.bot_config.value(uow, "AUTO_RENEWAL_ENABLED")):
            return 0
        days_before = int(await container.bot_config.value(uow, "AUTO_RENEWAL_DAYS_BEFORE"))
    now = utcnow()
    horizon = now + dt.timedelta(days=max(0, days_before))
    async with container.uow() as uow:
        rows = (
            await uow.session.execute(
                select(Subscription.id).where(
                    Subscription.autopay_enabled.is_(True),
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE, SubscriptionStatus.LIMITED]
                    ),
                    Subscription.expire_at.is_not(None),
                    Subscription.expire_at >= now,
                    Subscription.expire_at <= horizon,
                )
            )
        ).all()
    renewed = 0
    for (sub_id,) in rows:
        try:
            if await _autopay_one(container, sub_id):
                renewed += 1
        except Exception:
            log.warning("autopay_failed", subscription_id=sub_id, exc_info=True)
    log.info("process_autopay", candidates=len(rows), renewed=renewed)
    return renewed


async def _autopay_one(container: object, subscription_id: int) -> bool:
    """Charge + renew one subscription: from balance, else the saved card (opt-in).

    Returns True on a successful renewal."""
    from typing import cast

    from src.application.dto.pricing import PurchaseRequest
    from src.application.services.subscription import _plan_snapshot
    from src.core.enums import Currency, PurchaseType
    from src.core.enums import TransactionStatus as TS
    from src.core.enums import TransactionType as TT
    from src.infrastructure.database.base import utcnow
    from src.infrastructure.database.models.transaction import Transaction
    from src.infrastructure.di import AppContainer

    c = cast(AppContainer, container)
    async with c.uow() as uow:
        sub = await uow.subscriptions.get(subscription_id)
        if sub is None or not sub.autopay_enabled or sub.plan_id is None:
            return False
        user = await uow.users.get(sub.user_id)
        plan = await uow.plans.get_with_durations(sub.plan_id)
        if user is None or plan is None or not plan.durations:
            return False
        duration = next(
            (d for d in plan.durations if d.days == sub.autopay_period_days), plan.durations[0]
        )
        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan.id,
            duration_days=duration.days,
            currency=Currency.RUB,
            purchase_type=PurchaseType.RENEW,
            subscription_id=sub.id,
        )
        quote = await c.pricing.quote(uow, req)
        price = quote.final.amount_minor
        if user.balance_minor < price:
            # The card path (below) may only run once a day per subscription — the task
            # fires every 2h and a declining card must not be hammered all window long.
            attempt_due = (
                sub.autopay_card_attempted_at is None
                or utcnow() - sub.autopay_card_attempted_at >= dt.timedelta(hours=20)
            )
            if not (
                sub.autopay_card_enabled
                and user.saved_payment_method_id is not None
                and attempt_due
            ):
                return False  # insufficient balance — user keeps autopay on for next window
            # Card path below: it owns its transaction lifecycle — leave this uow first.
        else:
            await uow.users.increment_balance(user, -price)
            await uow.transactions.add(
                Transaction(
                    user_id=user.id,
                    type=TT.SUBSCRIPTION_PAYMENT,
                    status=TS.COMPLETED,
                    amount_minor=price,
                    currency=Currency.RUB,
                    purchase_type=PurchaseType.RENEW,
                    plan_snapshot=_plan_snapshot(plan),
                    pricing={"duration_days": duration.days, "subscription_id": sub.id},
                )
            )
            await c.subscriptions.renew(uow, sub, days=duration.days, telegram_id=user.telegram_id)
            await uow.commit()
            telegram_id = user.telegram_id
            if telegram_id is not None:
                await c.notifier.notify_user(
                    telegram_id, "🔁 Автопродление выполнено — подписка продлена."
                )
            return True
    return await _autopay_charge_card(c, subscription_id)


_AUTOPAY_CARD_FAIL = (
    "⚠️ Автопродление: не удалось списать оплату с карты.\n"  # noqa: RUF001
    "Продли подписку вручную или пополни баланс, иначе доступ отключится."
)


async def _autopay_charge_card(c: AppContainer, subscription_id: int) -> bool:
    """Renew via the saved card: PENDING txn → charge without confirmation → the standard
    idempotent pipeline (PaymentService.process). Returns True on a completed renewal.

    A ``pending`` charge is left to the webhook/reconciler; a failed HTTP call leaves the
    txn PENDING with no external_id (same contract as the interactive gateway flow)."""
    import contextlib

    from src.application.common.payments import PaymentContext
    from src.application.dto.pricing import PurchaseRequest
    from src.core.enums import Currency, PaymentGatewayType, PurchaseType
    from src.core.exceptions import ConfigError
    from src.core.money import Money
    from src.infrastructure.database.base import utcnow
    from src.infrastructure.payments.crypto import decrypt_gateway_settings
    from src.infrastructure.payments.gateways.yookassa import YookassaGateway

    async with c.uow() as uow:
        sub = await uow.subscriptions.get(subscription_id)
        if sub is None or sub.plan_id is None:
            return False
        user = await uow.users.get(sub.user_id)
        if user is None or not user.saved_payment_method_id:
            return False
        row = await uow.payment_gateways.get_active(PaymentGatewayType.YOOKASSA)
        if row is None or PaymentGatewayType.YOOKASSA not in c.gateway_factory.supported():
            return False
        settings = decrypt_gateway_settings(c.secret_box, dict(row.settings))
        gateway = c.gateway_factory.create(PaymentGatewayType.YOOKASSA, settings)
        if not isinstance(gateway, YookassaGateway) or not gateway.recurrent_enabled:
            return False

        plan = await uow.plans.get_with_durations(sub.plan_id)
        if plan is None or not plan.durations:
            return False
        duration = next(
            (d for d in plan.durations if d.days == sub.autopay_period_days), plan.durations[0]
        )
        sub.autopay_card_attempted_at = utcnow()  # set BEFORE charging: never retry a limbo
        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan.id,
            duration_days=duration.days,
            currency=Currency.RUB,
            purchase_type=PurchaseType.RENEW,
            subscription_id=sub.id,
        )
        txn, quote = await c.purchase.start(uow, req)
        if quote.is_free:  # start() already completed + fulfilled it
            await uow.commit()
            return True
        txn.gateway_type = PaymentGatewayType.YOOKASSA
        txn.gateway_display_name = row.display_name or PaymentGatewayType.YOOKASSA.value
        txn.payment_method = "saved_card"
        await uow.commit()

        payment_id = txn.payment_id
        amount = Money(quote.final.amount_minor, txn.currency)
        title = str((txn.plan_snapshot or {}).get("name") or "VPN")
        duration_days = duration.days
        user_id, telegram_id = user.id, user.telegram_id
        method_token = user.saved_payment_method_id

    # Stored encrypted (see _store_saved_method); tolerate plaintext like gateway creds.
    if c.secret_box is not None:
        with contextlib.suppress(ConfigError):
            method_token = c.secret_box.decrypt(method_token)

    ctx = PaymentContext(
        payment_id=payment_id,
        amount=amount,
        description=f"{title} · {duration_days} дн. (автопродление)",
        user_id=user_id,
        telegram_id=telegram_id,
    )
    try:
        result = await gateway.charge_saved(ctx, method_token)
    except Exception:
        log.warning("autopay card charge failed", subscription_id=subscription_id, exc_info=True)
        if telegram_id is not None:
            await c.notifier.notify_user(telegram_id, _AUTOPAY_CARD_FAIL)
        return False

    async with c.uow() as uow:
        txn2 = await uow.transactions.get_by_payment_id(payment_id)
        if txn2 is not None and result.external_id and not txn2.external_id:
            txn2.external_id = result.external_id  # reconciler can now poll this payment
            await uow.commit()

    if result.status is TransactionStatus.PENDING:
        # Not terminal yet — the webhook/reconciler finishes it through the same pipeline.
        log.info("autopay card charge pending", subscription_id=subscription_id)
        return False

    async with c.uow() as uow:
        moved = await c.payments.process(uow, payment_id=payment_id, status=result.status)
        await uow.commit()

    if result.status is TransactionStatus.COMPLETED and moved:
        if telegram_id is not None:
            await c.notifier.notify_user(
                telegram_id,
                f"🔁 Автопродление: {amount.amount_minor / 100:.0f} ₽ списано с карты — "  # noqa: RUF001
                "подписка продлена.",
            )
        return True
    if telegram_id is not None:
        await c.notifier.notify_user(telegram_id, _AUTOPAY_CARD_FAIL)
    return False
