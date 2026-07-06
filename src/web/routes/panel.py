"""Inbound Remnawave panel webhook (HMAC-verified): apply user events to local state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.core.enums import SubscriptionStatus
from src.core.exceptions import WebhookVerificationError
from src.core.logging import get_logger
from src.infrastructure.database.base import utcnow
from src.infrastructure.di import AppContainer
from src.infrastructure.remnawave.client import _to_panel_user
from src.web.deps import get_container

router = APIRouter(prefix="/webhook", tags=["panel"])
log = get_logger(__name__)

# Panel event -> local subscription status. ``user.updated`` derives status from the payload.
_EVENT_STATUS: dict[str, SubscriptionStatus] = {
    "user.enabled": SubscriptionStatus.ACTIVE,
    "user.disabled": SubscriptionStatus.DISABLED,
    "user.expired": SubscriptionStatus.EXPIRED,
    "user.limited": SubscriptionStatus.LIMITED,
    "user.deleted": SubscriptionStatus.DELETED,
}


@router.post("/panel")
async def panel_webhook(
    request: Request, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    body = await request.body()
    try:
        container.panel_webhook.verify(body, dict(request.headers))
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    event = container.panel_webhook.parse(body)
    # user.created fires for users we didn't create — ignore unless IMPORTED (gotcha #19).
    if event.event == "user.created" and not event.is_imported:
        return {"accepted": True, "ignored": True}

    applied = False
    if event.event.startswith("user.") and event.payload.get("uuid"):
        applied = await _apply_user_event(container, event.event, event.payload)
    log.info("panel_event", event_name=event.event, applied=applied)
    return {"accepted": True, "applied": applied}


async def _apply_user_event(
    container: AppContainer, event_name: str, payload: dict[str, Any]
) -> bool:
    """Keep the local subscription in sync with a panel-side user change. Idempotent."""
    try:
        panel_user = _to_panel_user(payload)
    except (KeyError, ValueError, TypeError):
        return False
    async with container.uow() as uow:
        sub = await uow.subscriptions.get_by_remnawave_uuid(panel_user.uuid)
        if sub is None:
            return False
        status = _EVENT_STATUS.get(event_name)
        if status is not None:
            sub.status = status
        elif event_name in ("user.updated", "user.modified"):
            sub.status = (
                SubscriptionStatus.ACTIVE if panel_user.is_enabled else SubscriptionStatus.DISABLED
            )
        sub.traffic_used_bytes = panel_user.traffic_used_bytes
        if panel_user.expire_at is not None:
            sub.expire_at = panel_user.expire_at
        sub.last_webhook_at = utcnow()
        await uow.commit()
    return True
