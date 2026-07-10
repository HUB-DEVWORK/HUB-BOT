"""Admin: maintenance actions and report topics (screen 14).

Heavy/irreversible host operations (update, restarts, reboot) are recorded to the audit
journal and executed only where the runtime actually can (e.g. process restart via the
supervisor is a deploy concern). What can't run in-process returns ``scheduled`` so the
UI still gets an honest, actionable response.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.infrastructure.database.models.report_topic import ReportTopic
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter()

# Fixed report-topic kinds (seeded on first read; admins bind topic ids/schedules).
_TOPIC_SEED: tuple[tuple[str, str], ...] = (
    ("daily_report", "21:00"),
    ("backups", "04:00"),
    ("payments", "instant"),
    ("tickets", "instant"),
    ("alerts", "instant"),
    ("weekly_report", "Mon 10:00"),
    ("registrations", "hourly"),
)


async def bootstrap_report_topics(container: AppContainer) -> None:
    """Seed the fixed report-topic kinds on boot (idempotent, adds only missing codes).

    Runs in the web lifespan so scheduled/instant reports (backups, alerts, daily summary)
    deliver on a fresh server before an admin ever opens screen 14 (RPT-1).
    """
    async with container.uow() as uow:
        existing = {t.code for t in await uow.report_topics.list()}
        added = False
        for code, sched in _TOPIC_SEED:
            if code not in existing:
                await uow.report_topics.add(ReportTopic(code=code, schedule=sched))
                added = True
        if added:
            await uow.commit()


async def bootstrap_public_urls(container: AppContainer) -> None:
    """Auto-wire the bot <-> mini-app link from WEB__PUBLIC_URL on first boot.

    Sets SUBSCRIPTION_MINI_APP_URL=<url>/app and CABINET_URL=<url> when the owner hasn't set
    them, so the bot shows the mini-app button (and OAuth/web cabinet work) out of the box. Only
    fills empties (never overrides a manual value); only for an https URL (Telegram WebApp needs
    TLS). Idempotent — a no-op once set.
    """
    base = (container.settings.web.public_url or "").strip().rstrip("/")
    if not base.startswith("https://"):
        return
    async with container.uow() as uow:
        cfg = container.bot_config
        updates: dict[str, str] = {}
        if not str(await cfg.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "").strip():
            updates["SUBSCRIPTION_MINI_APP_URL"] = f"{base}/app"
        if not str(await cfg.value(uow, "CABINET_URL") or "").strip():
            updates["CABINET_URL"] = base
        if updates:
            await cfg.set_values(uow, updates)
            await uow.commit()


@router.get("/report-topics")
async def list_report_topics(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    await bootstrap_report_topics(container)
    async with container.uow() as uow:
        rows = list(await uow.report_topics.list())
        group_id = await container.bot_config.value(uow, "REPORT_GROUP_ID")
    return {
        "group_id": group_id,
        "items": [
            {
                "id": t.id,
                "code": t.code,
                "topic_id": t.topic_id,
                "schedule": t.schedule,
                "enabled": t.enabled,
            }
            for t in rows
        ],
    }


class TopicPatch(BaseModel):
    topic_id: int | None = None
    schedule: str | None = Field(None, max_length=64)
    enabled: bool | None = None


@router.patch("/report-topics/{topic_id}")
async def patch_report_topic(
    topic_id: int,
    body: TopicPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    data = body.model_dump(exclude_unset=True)
    async with container.uow() as uow:
        t = await uow.report_topics.get(topic_id)
        if t is None:
            raise HTTPException(404, "topic not found")
        for k, v in data.items():
            setattr(t, k, v)
        await audit(uow, identity, "report_topic.patch", f"topic:{t.code}", **data)
        await uow.commit()
    return OkOut()


class GroupIn(BaseModel):
    group_id: str = Field(..., max_length=32)


@router.post("/report-topics/group")
async def set_report_group(
    body: GroupIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {"REPORT_GROUP_ID": body.group_id})
        await audit(uow, identity, "report_topic.group", None, group_id=body.group_id)
        await uow.commit()
    return OkOut()


# --- maintenance actions -------------------------------------------------------


@router.post("/maintenance/backup")
async def backup_now(
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    from src.infrastructure.taskiq.tasks import run_backup

    async with container.uow() as uow:
        await audit(uow, identity, "maintenance.backup", None)
        await uow.commit()
    task = await run_backup.kiq()
    return {"ok": True, "task_id": task.task_id}


class MaintenanceModeIn(BaseModel):
    enabled: bool


@router.post("/maintenance/mode")
async def maintenance_mode(
    body: MaintenanceModeIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {"MAINTENANCE_MODE": body.enabled})
        await audit(uow, identity, "maintenance.mode", None, enabled=body.enabled)
        await uow.commit()
    return OkOut()


@router.post("/maintenance/{action}")
async def maintenance_action(
    action: str,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    if action not in ("update", "restart-panel", "restart-bot", "reboot-server"):
        raise HTTPException(404, "unknown action")
    async with container.uow() as uow:
        await audit(uow, identity, f"maintenance.{action}", None)
        await uow.commit()
    # Host-level execution is wired at deploy time (systemd/compose control socket).
    return {"ok": True, "status": "scheduled", "action": action}


# Bot migration moved to routes/admin/migration.py (shopbot/bedolaga/remnashop/3x-ui).
