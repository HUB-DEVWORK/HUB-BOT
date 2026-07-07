"""Admin: subscription-expiry reminder steps (hour-based, screen 08).

A flexible ladder the owner edits: each step fires ``hours_before`` hours before a
subscription's ``expire_at`` (0 = at the moment of expiry). Seeded with 24 h / 12 h / 1 h
+ an at-expiry notice on first boot so a fresh shop warns subscribers out of the box.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.infrastructure.database.models.reminder_step import ReminderStep
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/reminders")

# Seeded on first boot — the owner gets a working ladder, then edits/adds/removes steps.
DEFAULT_STEPS: tuple[tuple[int, str], ...] = (
    (24, "Подписка истекает через 24 часа. Продлите, чтобы не потерять доступ."),
    (12, "Подписка истекает через 12 часов — успейте продлить."),
    (1, "Подписка истекает через час! Продлите сейчас, чтобы не остаться без VPN."),
    (0, "Подписка закончилась. Продлите — и доступ вернётся сразу."),
)


async def bootstrap_reminders(container: AppContainer) -> None:
    """Seed the default reminder ladder on first boot. No-op once any step exists."""
    async with container.uow() as uow:
        if await uow.reminders.count() > 0:
            return
        for hours, text in DEFAULT_STEPS:
            await uow.reminders.add(ReminderStep(hours_before=hours, text=text))
        await uow.commit()


def _serialize(s: ReminderStep) -> dict[str, Any]:
    return {
        "id": s.id,
        "hours_before": s.hours_before,
        "text": s.text,
        "button_enabled": s.button_enabled,
        "enabled": s.enabled,
    }


class ReminderIn(BaseModel):
    hours_before: int = Field(ge=0, le=8760)  # 0 = at expiry … up to a year out
    text: str = Field(min_length=1, max_length=4096)
    button_enabled: bool = True
    enabled: bool = True


class ReminderPatch(BaseModel):
    hours_before: int | None = Field(None, ge=0, le=8760)
    text: str | None = Field(None, min_length=1, max_length=4096)
    button_enabled: bool | None = None
    enabled: bool | None = None


@router.get("")
async def list_reminders(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        steps = await uow.reminders.ordered()
    return {"items": [_serialize(s) for s in steps]}


@router.post("")
async def create_reminder(
    body: ReminderIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        if await uow.reminders.find_one(hours_before=body.hours_before) is not None:
            raise HTTPException(409, "a step for this offset already exists")
        step = ReminderStep(
            hours_before=body.hours_before,
            text=body.text,
            button_enabled=body.button_enabled,
            enabled=body.enabled,
        )
        await uow.reminders.add(step)
        await audit(uow, identity, "reminder.create", None, hours_before=body.hours_before)
        await uow.commit()
        return _serialize(step)


@router.patch("/{step_id}")
async def update_reminder(
    step_id: int,
    body: ReminderPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        step = await uow.reminders.get(step_id)
        if step is None:
            raise HTTPException(404, "reminder not found")
        if body.hours_before is not None and body.hours_before != step.hours_before:
            clash = await uow.reminders.find_one(hours_before=body.hours_before)
            if clash is not None:
                raise HTTPException(409, "a step for this offset already exists")
            step.hours_before = body.hours_before
        if body.text is not None:
            step.text = body.text
        if body.button_enabled is not None:
            step.button_enabled = body.button_enabled
        if body.enabled is not None:
            step.enabled = body.enabled
        await audit(uow, identity, "reminder.update", str(step_id))
        await uow.commit()
        return _serialize(step)


@router.delete("/{step_id}")
async def delete_reminder(
    step_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    async with container.uow() as uow:
        step = await uow.reminders.get(step_id)
        if step is None:
            raise HTTPException(404, "reminder not found")
        await uow.reminders.delete_by(id=step_id)
        await audit(uow, identity, "reminder.delete", str(step_id))
        await uow.commit()
    return {"ok": True}
