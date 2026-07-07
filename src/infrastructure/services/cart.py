"""Redis-backed «smart cart»: remember a purchase intent when balance is short.

When a user tries to buy from balance and can't afford it, we stash the exact
PurchaseRequest (with its frozen constructor selection) under ``cart:<user_id>``.
After a top-up credits the wallet, the deposit path pops it and completes the
purchase automatically — the buyer pays once and the subscription just appears.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.application.dto.pricing import PurchaseRequest

_KEY = "cart:{}"


def _to_payload(req: PurchaseRequest) -> dict[str, Any]:
    return {
        "plan_id": req.plan_id,
        "duration_days": req.duration_days,
        "purchase_type": req.purchase_type.value,
        "subscription_id": req.subscription_id,
        "constructor_period_id": req.constructor_period_id,
        "traffic_pack_id": req.traffic_pack_id,
        "traffic_limit_bytes": req.traffic_limit_bytes,
        "device_limit": req.device_limit,
        "internal_squads": list(req.internal_squads),
        "external_squad": req.external_squad,
    }


def _from_payload(user_id: int, data: dict[str, Any]) -> PurchaseRequest:
    from src.application.dto.pricing import PurchaseRequest
    from src.core.enums import Currency, PurchaseType

    return PurchaseRequest(
        user_id=user_id,
        plan_id=int(data["plan_id"]),
        duration_days=int(data["duration_days"]),
        currency=Currency.RUB,
        purchase_type=PurchaseType(data.get("purchase_type") or "new"),
        subscription_id=data.get("subscription_id"),
        constructor_period_id=data.get("constructor_period_id"),
        traffic_pack_id=data.get("traffic_pack_id"),
        traffic_limit_bytes=data.get("traffic_limit_bytes"),
        device_limit=data.get("device_limit"),
        internal_squads=tuple(data.get("internal_squads") or ()),
        external_squad=data.get("external_squad"),
    )


async def save_cart(redis: Redis, req: PurchaseRequest, ttl_seconds: int) -> None:
    await redis.set(_KEY.format(req.user_id), json.dumps(_to_payload(req)), ex=max(60, ttl_seconds))


async def pop_cart(redis: Redis, user_id: int) -> PurchaseRequest | None:
    key = _KEY.format(user_id)
    raw = await redis.get(key)
    if raw is None:
        return None
    await redis.delete(key)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return _from_payload(user_id, data)
