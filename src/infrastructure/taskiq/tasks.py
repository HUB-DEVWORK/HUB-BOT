"""Background tasks. Import path registered with the worker (see compose.local.yml)."""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from src.core.enums import TransactionStatus, TransactionType
from src.core.logging import get_logger
from src.infrastructure.taskiq.broker import broker, get_container

log = get_logger(__name__)


@broker.task
async def process_payment(payment_id: str, status: str) -> bool:
    """Complete a transaction from a verified webhook (idempotent CAS + fulfilment).

    Enqueued by the payment webhook route; never run inline. Safe to retry — a duplicate
    finds the transaction already terminal and no-ops. Notifies the buyer AFTER commit (only
    the out-of-band webhook path reaches here; in-bot flows reply in the handler themselves).
    """
    container = get_container()
    pid = UUID(payment_id)
    async with container.uow() as uow:
        moved = await container.payments.process(
            uow, payment_id=pid, status=TransactionStatus(status)
        )
        await uow.commit()
    log.info("process_payment", payment_id=payment_id, status=status, advanced=moved)

    if moved and status == TransactionStatus.COMPLETED.value:
        await _notify_paid(container, pid)
    return moved


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

    Returns the archive path, or None when pg_dump is unavailable/failed. Sending the
    archive to the report group happens in the reports job, not here.
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
    return str(out)


@broker.task
async def send_broadcast(broadcast_id: int) -> None:
    """Deliver a broadcast to its audience, updating live progress counters.

    Uses a bare aiogram ``Bot`` (no dispatcher) so the worker doesn't need the bot
    process. Progress is committed in batches so the cabinet's polling sees it grow;
    per-user failures (blocked bot, deactivated account) increment ``failed``.
    """
    import asyncio

    from aiogram import Bot
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from src.core.enums import BroadcastStatus
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
        await uow.commit()

    markup = None
    if button_enabled and button_text and button_url:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=button_text, url=button_url)]]
        )

    sent = failed = 0
    bot = Bot(token=container.settings.bot.token)
    try:
        for i, chat_id in enumerate(chat_ids, start=1):
            try:
                await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
                sent += 1
            except TelegramRetryAfter as exc:  # flood control — wait and retry once
                await asyncio.sleep(exc.retry_after)
                try:
                    await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
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
