"""Admin: broadcasts composer + history with live progress (screen 07).

Creating a broadcast enqueues a taskiq job (``send_broadcast``); the worker walks the
audience and bumps ``sent``/``failed``. The UI polls ``GET /broadcasts/{id}`` while
``status`` is pending/running.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.core.enums import (
    BroadcastAudience,
    BroadcastMedia,
    BroadcastStatus,
    SubscriptionStatus,
    UserStatus,
)
from src.infrastructure.database.models.broadcast import Broadcast
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/broadcasts")

_AUD_SUB_STATUSES: dict[BroadcastAudience, tuple[SubscriptionStatus, ...]] = {
    BroadcastAudience.ACTIVE: (SubscriptionStatus.ACTIVE, SubscriptionStatus.LIMITED),
    BroadcastAudience.TRIAL: (SubscriptionStatus.TRIAL,),
    BroadcastAudience.EXPIRED: (SubscriptionStatus.EXPIRED, SubscriptionStatus.DISABLED),
}


def audience_stmt(audience: BroadcastAudience) -> Any:
    """Select of telegram_ids for an audience (shared with the worker)."""
    stmt = select(User.telegram_id).where(
        User.telegram_id.is_not(None), User.status == UserStatus.ACTIVE
    )
    if audience is not BroadcastAudience.ALL:
        stmt = stmt.join(Subscription, Subscription.id == User.current_subscription_id).where(
            Subscription.status.in_(_AUD_SUB_STATUSES[audience])
        )
    return stmt


def _row(b: Broadcast) -> dict[str, Any]:
    return {
        "id": b.id,
        "audience": b.audience.value,
        "media": b.media.value,
        "text": b.text,
        "media_path": b.media_path,
        "button_enabled": b.button_enabled,
        "button_text": b.button_text,
        "button_action": b.button_action,
        "button_color": b.button_color,
        "emoji_id": b.emoji_id,
        "status": b.status.value,
        "total": b.total,
        "sent": b.sent,
        "failed": b.failed,
        "progress_pct": round((b.sent + b.failed) * 100 / b.total, 1) if b.total else 0.0,
        "created_at": iso(b.created_at),
        "started_at": iso(b.started_at),
        "finished_at": iso(b.finished_at),
    }


@router.get("/audiences")
async def audiences(container: AppContainer = Depends(get_container)) -> dict[str, int]:
    """Counters for the composer segment control."""
    out: dict[str, int] = {}
    async with container.uow() as uow:
        for audience in BroadcastAudience:
            stmt = audience_stmt(audience)
            out[audience.value] = int(
                await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            )
    return out


@router.get("")
async def list_broadcasts(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        items = [_row(b) for b in await uow.broadcasts.recent(30)]
    return {"items": items}


@router.get("/{broadcast_id}")
async def get_broadcast(
    broadcast_id: int, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        b = await uow.broadcasts.get(broadcast_id)
        if b is None:
            raise HTTPException(404, "broadcast not found")
        return _row(b)


class BroadcastIn(BaseModel):
    audience: BroadcastAudience = BroadcastAudience.ALL
    media: BroadcastMedia = BroadcastMedia.TEXT
    text: str = Field(..., min_length=1, max_length=4096)
    media_path: str | None = Field(None, max_length=512)
    button_enabled: bool = False
    button_text: str | None = Field(None, max_length=64)
    button_url: str | None = Field(None, max_length=512)
    button_action: str | None = Field(None, max_length=64)
    button_color: str | None = Field(None, max_length=9)
    emoji_id: str | None = Field(None, max_length=32)


@router.post("")
async def create_broadcast(
    body: BroadcastIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        stmt = audience_stmt(body.audience)
        total = int(
            await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        if total == 0:
            raise HTTPException(400, "audience is empty")
        if body.media is not BroadcastMedia.TEXT and not (body.media_path or "").startswith(
            "uploads/"
        ):
            raise HTTPException(400, "media broadcast requires an uploaded file")
        if body.button_enabled and not (body.button_url or body.button_action):
            raise HTTPException(400, "button needs a url or an action")
        b = Broadcast(
            audience=body.audience,
            media=body.media,
            text=body.text,
            media_path=body.media_path,
            button_enabled=body.button_enabled,
            button_text=body.button_text,
            button_url=body.button_url,
            button_action=body.button_action,
            button_color=body.button_color,
            emoji_id=body.emoji_id,
            status=BroadcastStatus.PENDING,
            total=total,
            created_by_id=identity.user_id,
        )
        await uow.broadcasts.add(b)
        await audit(
            uow,
            identity,
            "broadcast.create",
            f"broadcast:{b.id}",
            audience=body.audience.value,
            total=total,
        )
        await uow.commit()
        broadcast_id = b.id

    # Enqueue delivery (import here: the web app must not import the bot at module load).
    from src.infrastructure.taskiq.tasks import send_broadcast

    await send_broadcast.kiq(broadcast_id)
    async with container.uow() as uow:
        b2 = await uow.broadcasts.get(broadcast_id)
        assert b2 is not None
        return _row(b2)
