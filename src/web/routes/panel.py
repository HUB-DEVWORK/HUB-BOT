"""Inbound Remnawave panel webhook (HMAC-verified). Dedups and dispatches events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.core.exceptions import WebhookVerificationError
from src.core.logging import get_logger
from src.infrastructure.di import AppContainer
from src.web.deps import get_container

router = APIRouter(prefix="/webhook", tags=["panel"])
log = get_logger(__name__)


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
        log.debug("ignoring foreign user.created")
        return {"accepted": True, "ignored": True}

    # Base handles verification + parsing; concrete event handlers (enable/disable/expiry/
    # node/torrent-blocker) are wired when the bot ships. Dedup via redis_lock there.
    log.info("panel_event", event_name=event.event)
    return {"accepted": True}
