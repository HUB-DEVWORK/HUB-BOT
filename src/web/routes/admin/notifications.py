"""Admin: owner-editable notification templates (per lifecycle stage, screen 08).

Every user-facing message the bot sends on a lifecycle event is a row here — the owner
edits the text (with ``{placeholders}``) and toggles it on/off from the cabinet or the
in-bot admin, instead of the text being hardcoded. ``bootstrap_notifications`` seeds any
missing event on boot with a nice default, so adding a new event in code needs no data
migration and never overwrites the owner's edits.
"""

# ruff: noqa: RUF001  — Russian UI templates mix Cyrillic with Latin {placeholders} by design.

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.infrastructure.database.models.notification_template import NotificationTemplate
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/notifications")

# (event, RU title, default text, available placeholders). Order = display order.
NOTIFICATION_EVENTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "welcome",
        "Приветствие (регистрация)",
        "👋 Привет, {name}! Это VPN-бот. Оформи подписку за пару тапов — выбери действие в меню.",
        ("name",),
    ),
    (
        "trial_started",
        "Пробный период выдан",
        "🎁 Пробный период активирован на {days} дн. Приятного пользования!",
        ("name", "days"),
    ),
    (
        "purchase",
        "Оплата подписки",
        "✅ Оплата получена — подписка активна! Нажми «Подключить», чтобы настроить приложение.",
        ("name", "plan", "expire"),
    ),
    (
        "balance_topup",
        "Пополнение баланса",
        "💰 Баланс пополнен на {amount}. Текущий баланс: {balance}.",
        ("name", "amount", "balance"),
    ),
    (
        "renewal",
        "Продление подписки",
        "🔄 Подписка продлена до {expire}. Спасибо, что остаёшься с нами!",
        ("name", "plan", "expire"),
    ),
    (
        "autopay_success",
        "Автопродление выполнено",
        "🔁 Автопродление выполнено — подписка продлена до {expire}.",
        ("plan", "expire"),
    ),
    (
        "autopay_failed",
        "Автопродление не удалось",
        "⚠️ Не удалось списать оплату по карте. Продли вручную или пополни баланс — "
        "иначе доступ отключится.",
        (),
    ),
    (
        "plan_changed",
        "Смена тарифа",
        "🔀 Тариф изменён на «{plan}». Подписка действует до {expire}.",
        ("plan", "expire"),
    ),
    (
        "traffic_topup",
        "Докупка трафика",
        "📶 Трафик добавлен к подписке. Приятного пользования!",
        (),
    ),
    (
        "referral_reward",
        "Реферальная награда",
        "🎉 Тебе начислено {amount} за приглашённого друга. Спасибо!",
        ("name", "amount"),
    ),
    (
        "refund",
        "Возврат средств",
        "↩️ Оформлен возврат {amount}. Если это ошибка — напиши в поддержку.",
        ("amount",),
    ),
    (
        "expired",
        "Подписка истекла",
        "⌛ Подписка закончилась. Продли — и доступ вернётся сразу.",
        ("name",),
    ),
)

_DEFAULT = {ev: text for ev, _, text, _ in NOTIFICATION_EVENTS}
_META = {ev: (title, ph) for ev, title, _, ph in NOTIFICATION_EVENTS}


def render(text: str, **values: Any) -> str:
    """Substitute ``{key}`` placeholders; leaves unknown ones untouched."""
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


async def notification_text(uow: UnitOfWork, event: str, **values: Any) -> str | None:
    """Rendered text for an event, or None when the owner disabled it (caller stays silent).

    Falls back to the code default when the row hasn't been seeded yet, so a notification
    never goes missing between a deploy and the next boot-seed.
    """
    row = await uow.notifications.by_event(event)
    if row is not None and not row.enabled:
        return None
    text = row.text if row is not None else _DEFAULT.get(event)
    return render(text, **values) if text else None


async def bootstrap_notifications(container: AppContainer) -> None:
    """Seed any canonical event missing from the table. Additive + idempotent — never
    overwrites an edited row, and auto-adds events introduced in later releases."""
    async with container.uow() as uow:
        have = {t.event for t in await uow.notifications.ordered()}
        added = False
        for event, _title, text, _ph in NOTIFICATION_EVENTS:
            if event not in have:
                await uow.notifications.add(NotificationTemplate(event=event, text=text))
                added = True
        if added:
            await uow.commit()


def _serialize(event: str, text: str, enabled: bool) -> dict[str, Any]:
    title, placeholders = _META[event]
    return {
        "event": event,
        "title": title,
        "text": text,
        "enabled": enabled,
        "placeholders": list(placeholders),
    }


@router.get("")
async def list_notifications(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        rows = {t.event: t for t in await uow.notifications.ordered()}
    items = [
        _serialize(
            ev,
            rows[ev].text if ev in rows else default,
            rows[ev].enabled if ev in rows else True,
        )
        for ev, _title, default, _ph in NOTIFICATION_EVENTS
    ]
    return {"items": items}


class NotificationPatch(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=4096)
    enabled: bool | None = None


@router.patch("/{event}")
async def update_notification(
    event: str,
    body: NotificationPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    if event not in _DEFAULT:
        raise HTTPException(404, "unknown notification event")
    async with container.uow() as uow:
        row = await uow.notifications.by_event(event)
        if row is None:
            row = NotificationTemplate(event=event, text=_DEFAULT[event])
            await uow.notifications.add(row)
        if body.text is not None:
            row.text = body.text
        if body.enabled is not None:
            row.enabled = body.enabled
        await audit(uow, identity, "notification.update", event)
        await uow.commit()
        return _serialize(event, row.text, row.enabled)
