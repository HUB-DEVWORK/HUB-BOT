"""Mini-app cabinet API: Telegram initData auth + subscriber self-service.

Auth: every request carries ``Authorization: tma <initData>`` (Telegram Mini Apps).
The user is resolved (or created) by the verified telegram id — the same row the bot
maintains. Endpoints mirror miniapp/CONTRACT.md and power all 8 visual themes.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.application.common.payments import PaymentContext, PaymentResultKind
from src.application.dto.pricing import PurchaseRequest
from src.application.events import UserRegistered
from src.application.services.connection import build_deep_links
from src.application.services.ids import generate_referral_code
from src.application.services.promo import PromoError
from src.core.enums import Currency, Locale, PurchaseType, UserStatus
from src.core.exceptions import (
    DomainError,
    InsufficientBalance,
    InvalidStateTransition,
    RemnawaveError,
)
from src.core.logging import get_logger
from src.core.money import Money
from src.core.security import validate_init_data
from src.infrastructure.database.models.plan import Plan
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.infrastructure.payments.crypto import decrypt_gateway_settings
from src.web.deps import get_container

log = get_logger(__name__)

router = APIRouter(prefix="/api/cabinet", tags=["cabinet"])

GIB = 1024**3


def _proxy_link(raw: str) -> str:
    """Normalize an MTProto proxy link to the https://t.me/proxy?... form."""
    raw = raw.strip()
    if raw.startswith("tg://proxy"):
        return "https://t.me/proxy" + raw.removeprefix("tg://proxy")
    if raw.startswith("t.me/"):
        return "https://" + raw
    return raw


async def cabinet_user(request: Request, container: AppContainer = Depends(get_container)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        raise HTTPException(401, "unauthorized")
    data = validate_init_data(auth.removeprefix("tma "), container.settings.bot.token)
    if data is None or "user_parsed" not in data:
        raise HTTPException(401, "bad initData")
    tg: dict[str, Any] = data["user_parsed"]
    tg_id = int(tg["id"])

    async with container.uow() as uow:
        user = await uow.users.get_by_telegram_id(tg_id)
        created = user is None
        if user is None:
            user = User(
                telegram_id=tg_id,
                username=tg.get("username"),
                first_name=tg.get("first_name"),
                last_name=tg.get("last_name"),
                language=Locale.EN if (tg.get("language_code") or "ru")[:2] == "en" else Locale.RU,
                referral_code=generate_referral_code(),
            )
            await uow.users.add(user)
        await uow.commit()
    if created:
        # First contact came through the mini-app — same event the bot middleware emits.
        await container.event_bus.publish(UserRegistered(user_id=user.id, telegram_id=tg_id))
    if user.status is UserStatus.BLOCKED:
        raise HTTPException(403, "blocked")
    return user


def _sub_payload(sub: Any) -> dict[str, Any] | None:
    if sub is None:
        return None
    return {
        "status": sub.status.value,
        "is_trial": sub.is_trial,
        "plan_id": sub.plan_id,
        "plan_name": (sub.plan_snapshot or {}).get("name"),
        "start_at": sub.start_at.isoformat() if sub.start_at else None,
        "expire_at": sub.expire_at.isoformat() if sub.expire_at else None,
        "device_limit": sub.device_limit,
        "traffic": {
            "used_bytes": sub.traffic_used_bytes,
            "limit_bytes": sub.traffic_limit_bytes,
            "unlimited": not sub.traffic_limit_bytes,
        },
        "subscription_url": sub.subscription_url,
        "crypto_link": sub.crypto_link,
    }


@router.get("/me")
async def me(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
        cfg = container.bot_config
        miniapp = await uow.miniapp.get_or_create()
        trial_enabled = bool(await cfg.value(uow, "TRIAL_ENABLED"))
        bot_username = str(await cfg.value(uow, "BOT_USERNAME") or "")
        proxy_enabled = bool(await cfg.value(uow, "MTPROTO_PROXY_ENABLED"))
        proxy_url = str(await cfg.value(uow, "MTPROTO_PROXY_URL") or "")
        sales_mode = str(await cfg.value(uow, "SALES_MODE"))
        gateways = [
            {"id": g.type.value, "label": g.display_name or g.type.value}
            for g in await uow.payment_gateways.list()
            if g.is_active
            and g.type in container.gateway_factory.supported()
            and g.type.value not in ("manual", "telegram_stars")
        ]
        await uow.commit()
    return {
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "language": user.language.value,
            "currency": user.currency.value,
            "balance_minor": user.balance_minor,
            "referral_code": user.referral_code,
            "personal_discount_pct": user.personal_discount_pct,
            "is_trial_available": trial_enabled and user.is_trial_available,
        },
        "subscription": _sub_payload(sub),
        "app": {
            "template": miniapp.template,
            "title": miniapp.title,
            "greeting": miniapp.greeting,
            "accent_color": miniapp.accent_color,
            "bot_username": bot_username,
            "mtproto_proxy": _proxy_link(proxy_url) if proxy_enabled and proxy_url else None,
            "ui": miniapp.ui or {},
            "payment_methods": gateways,
            "balance_enabled": bool(await container.bot_config.value(uow, "BALANCE_ENABLED")),
            "sales_mode": sales_mode,
        },
    }


@router.get("/plans")
async def plans(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        rows = [p for p in await uow.plans.list_with_durations() if p.is_active and not p.is_trial]
        stars_rate = int(await container.bot_config.value(uow, "STARS_RATE_RUB"))
    items = []
    for p in sorted(rows, key=lambda p: p.order_index):
        durations = []
        for d in p.durations:
            rub = next((pr.price_minor for pr in d.prices if pr.currency is Currency.RUB), None)
            if rub is None:
                continue
            durations.append(
                {
                    "days": d.days,
                    "months": round(d.days / 30) or 1,
                    "price_minor": rub,
                    "price_stars": max(1, math.ceil(rub / max(1, stars_rate))),
                }
            )
        items.append(
            {
                "id": p.id,
                "public_code": p.public_code,
                "name": p.name,
                "description": p.description,
                "traffic_limit_bytes": p.traffic_limit_bytes,
                "device_limit": p.device_limit,
                "is_current": bool(user.current_subscription_id and _is_current(user, p)),
                "durations": durations,
            }
        )
    return {"currency": "RUB", "items": items}


def _is_current(user: User, plan: Plan) -> bool:
    # Lightweight check without extra queries: snapshot comparison happens client-side;
    # here we only mark by plan id when the user has a live subscription on it.
    return False  # refined by /me subscription.plan_id on the client


@router.get("/constructor")
async def constructor(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    """Constructor-mode price list: active periods + traffic packs (SALES_MODE=constructor)."""
    async with container.uow() as uow:
        periods = [p for p in await uow.constructor_periods.list() if p.is_active]
        packs = [t for t in await uow.traffic_packs.list() if t.is_active]
        stars_rate = int(await container.bot_config.value(uow, "STARS_RATE_RUB"))
    return {
        "currency": "RUB",
        "stars_rate": max(1, stars_rate),
        "periods": [
            {
                "id": p.id,
                "days": p.days,
                "months": round(p.days / 30) or 1,
                "price_minor": p.price_minor,
            }
            for p in sorted(periods, key=lambda p: p.days)
        ],
        "traffic_packs": [
            {"id": t.id, "gb": t.gb, "price_minor": t.price_minor}
            for t in sorted(packs, key=lambda t: (t.gb == 0, t.gb))
        ],
    }


@router.get("/referral")
async def referral(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        cfg = container.bot_config
        bot_username = str(await cfg.value(uow, "BOT_USERNAME") or "")
        bonus_days = int(await cfg.value(uow, "REFERRAL_BONUS_DAYS"))
        percent = int(await cfg.value(uow, "REFERRAL_PERCENT"))
        invited = await uow.users.count(referred_by_id=user.id)
        earnings = await uow.referral_earnings.list(user_id=user.id, limit=1000)
    return {
        "code": user.referral_code,
        "link": f"https://t.me/{bot_username}?start=ref_{user.referral_code}",
        "bonus_days": bonus_days,
        "commission_percent": percent,
        "invited_count": invited,
        "earnings_minor": sum(e.amount_minor for e in earnings),
    }


@router.get("/payments")
async def payments_history(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        txs = await uow.transactions.list_recent(user.id, limit=20)
    return {
        "items": [
            {
                "id": t.id,
                "type": t.type.value,
                "status": t.status.value,
                "amount_minor": t.amount_minor,
                "currency": t.currency.value,
                "method": t.payment_method or (t.gateway_type.value if t.gateway_type else None),
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in sorted(txs, key=lambda t: t.id, reverse=True)
        ]
    }


class PurchaseIn(BaseModel):
    # Plans mode: plan_id + days. Constructor mode: period_id + pack_id instead.
    plan_id: int | None = None
    days: int | None = Field(None, ge=1, le=3650)
    period_id: int | None = None
    pack_id: int | None = None
    method: str = Field("balance", max_length=32)  # balance | stars | <gateway type>


@router.post("/purchase")
async def purchase(
    body: PurchaseIn,
    user: User = Depends(cabinet_user),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    if body.period_id is not None and body.pack_id is not None:
        async with container.uow() as uow:
            device_limit = int(await container.bot_config.value(uow, "DEFAULT_DEVICE_LIMIT"))
            try:
                req = await container.purchase.build_constructor_request(
                    uow,
                    user_id=user.id,
                    period_id=body.period_id,
                    pack_id=body.pack_id,
                    device_limit=device_limit,
                )
            except DomainError as exc:
                raise HTTPException(400, str(exc)) from exc
            await uow.commit()  # the hidden constructor plan may have just been created
    elif body.plan_id is not None and body.days is not None:
        async with container.uow() as uow:
            ptype, sub_id = await container.purchase.resolve_purchase_type(
                uow, user.id, body.plan_id
            )
        req = PurchaseRequest(
            user_id=user.id,
            plan_id=body.plan_id,
            duration_days=body.days,
            currency=Currency.RUB,
            purchase_type=ptype,
            subscription_id=sub_id,
        )
    else:
        raise HTTPException(400, "plan_id+days or period_id+pack_id required")

    if body.method == "balance":
        async with container.uow() as uow:
            if not bool(await container.bot_config.value(uow, "BALANCE_ENABLED")):
                raise HTTPException(400, "balance payments are disabled")
            try:
                # Shared checkout path — identical to the bot's balance purchase.
                await container.purchase.checkout_from_balance(uow, req)
            except InsufficientBalance as exc:
                raise HTTPException(402, "insufficient balance") from exc
            except InvalidStateTransition as exc:
                raise HTTPException(409, "already processed") from exc
            except RemnawaveError as exc:
                log.error("cabinet provision failed", error=str(exc))
                raise HTTPException(502, "provisioning temporarily unavailable") from exc
            except DomainError as exc:
                raise HTTPException(400, str(exc)) from exc
            await uow.commit()
        return {"ok": True, "paid_with": "balance"}

    if body.method not in ("balance", "stars"):
        return await _pay_with_gateway(container, user, req, body.method)

    # Stars: pending tx + invoice link opened via Telegram.WebApp.openInvoice.
    async with container.uow() as uow:
        try:
            txn, quote = await container.purchase.start(uow, req)
        except DomainError as exc:
            raise HTTPException(400, str(exc)) from exc
        if quote.is_free:
            await uow.commit()  # start() already fulfilled the 100%-discount purchase
            return {"ok": True, "paid_with": "free"}
        stars_rate = int(await container.bot_config.value(uow, "STARS_RATE_RUB"))
        title = str((txn.plan_snapshot or {}).get("name") or "VPN")
        await uow.commit()
        payment_id = str(txn.payment_id)
        stars = max(1, math.ceil(quote.final.amount_minor / max(1, stars_rate)))

    from aiogram import Bot
    from aiogram.types import LabeledPrice

    bot = Bot(token=container.settings.bot.token)
    try:
        link = await bot.create_invoice_link(
            title=f"{title} · {req.duration_days} дн.",
            description="Оплата VPN-подписки",
            payload=payment_id,
            currency="XTR",
            prices=[LabeledPrice(label="VPN", amount=stars)],
        )
    finally:
        await bot.session.close()
    return {"ok": True, "invoice_link": link}


async def _pay_with_gateway(
    container: AppContainer, user: User, req: PurchaseRequest, method: str
) -> dict[str, Any]:
    """Hosted payment: pending tx -> provider invoice -> redirect URL.

    The provider webhook completes the transaction through the standard idempotent
    pipeline; nothing is fulfilled here.
    """
    from src.core.enums import PaymentGatewayType

    try:
        gtype = PaymentGatewayType(method)
    except ValueError as exc:
        raise HTTPException(400, "unknown payment method") from exc

    async with container.uow() as uow:
        row = await uow.payment_gateways.get_active(gtype)
        if row is None or gtype not in container.gateway_factory.supported():
            raise HTTPException(400, "payment method is not enabled")
        settings = decrypt_gateway_settings(container.secret_box, dict(row.settings))
        try:
            txn, quote = await container.purchase.start(uow, req)
        except DomainError as exc:
            raise HTTPException(400, str(exc)) from exc
        if quote.is_free:
            await uow.commit()
            return {"ok": True, "paid_with": "free"}
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        title = str((txn.plan_snapshot or {}).get("name") or "VPN")
        gateway = container.gateway_factory.create(gtype, settings)
        try:
            result = await gateway.create_payment(
                PaymentContext(
                    payment_id=txn.payment_id,
                    amount=Money(quote.final.amount_minor, txn.currency),
                    description=f"{title} · {req.duration_days} дн.",
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    return_url=miniapp_url or None,
                )
            )
        except Exception as exc:
            log.error("gateway create_payment failed", gateway=method, error=str(exc))
            raise HTTPException(502, f"panel error: provider {method} failed") from exc
        if result.kind is not PaymentResultKind.REDIRECT or not result.redirect_url:
            raise HTTPException(502, f"provider {method} returned no payment url")
        txn.gateway_type = gtype
        txn.external_id = result.external_id
        txn.gateway_display_name = row.display_name or gtype.value
        await uow.commit()
        return {"ok": True, "redirect_url": result.redirect_url}


class PromoIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)


@router.post("/promocode")
async def apply_promocode(
    body: PromoIn,
    user: User = Depends(cabinet_user),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        u = await uow.users.get(user.id)
        assert u is not None
        try:
            reward = await container.promo.apply(uow, u, body.code.strip().upper())
        except PromoError as exc:
            return {"ok": False, "message": str(exc)}
        await uow.commit()
    return {"ok": True, "reward_type": reward.value}


@router.post("/trial")
async def activate_trial(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        cfg = container.bot_config
        if not bool(await cfg.value(uow, "TRIAL_ENABLED")):
            raise HTTPException(400, "trial disabled")
        # FOR UPDATE: two concurrent /trial calls must not both pass the check.
        u = await uow.users.lock_for_update(user.id)
        if u is None or not u.is_trial_available:
            raise HTTPException(400, "trial already used")
        days = int(await cfg.value(uow, "TRIAL_DURATION_DAYS"))
        traffic_gb = int(await cfg.value(uow, "TRIAL_TRAFFIC_GB"))
        devices = int(await cfg.value(uow, "TRIAL_DEVICE_LIMIT"))
        plan = await uow.plans.find_one(is_trial=True) or await uow.plans.find_one(name="Trial")
        if plan is not None and not plan.is_trial:
            plan.is_trial = True
        if plan is None:
            plan = Plan(
                public_code="trial",
                name="Trial",
                is_trial=True,
                is_active=False,
                traffic_limit_bytes=traffic_gb * GIB or None,
                device_limit=devices,
            )
            await uow.plans.add(plan)
        req = PurchaseRequest(
            user_id=u.id,
            plan_id=plan.id,
            duration_days=days,
            currency=Currency.RUB,
            purchase_type=PurchaseType.NEW,
        )
        try:
            sub = await container.subscriptions.grant(
                uow, user=u, plan=plan, req=req, is_trial=True
            )
        except RemnawaveError as exc:
            raise HTTPException(502, "provisioning temporarily unavailable") from exc
        await uow.commit()
        return {"ok": True, "days": days, "subscription": _sub_payload(sub)}


@router.get("/connection")
async def connection(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    """Step-2 data for the Connect tab: personal subscription URL + deep links."""
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
    if sub is None or not sub.status.is_usable or not sub.subscription_url:
        raise HTTPException(404, "no active subscription")
    url = sub.subscription_url
    return {
        "subscription_url": url,
        "expires_at": sub.expire_at.isoformat() if sub.expire_at else None,
        "deep_links": build_deep_links(url, sub.crypto_link),
    }


@router.get("/config")
async def app_config(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    """Public theming config (no auth) so the shell can paint before initData checks."""
    async with container.uow() as uow:
        miniapp = await uow.miniapp.get_or_create()
        await uow.commit()
    return {
        "template": miniapp.template,
        "title": miniapp.title,
        "greeting": miniapp.greeting,
        "accent_color": miniapp.accent_color,
        "published_at": miniapp.published_at.isoformat() if miniapp.published_at else None,
    }


@router.get("/devices")
async def list_devices(
    user: User = Depends(cabinet_user), container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    """HWID devices bound to the current subscription's panel user."""
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
    if sub is None or sub.remnawave_uuid is None:
        return {"items": [], "device_limit": None}
    try:
        devices = await container.remnawave_client.get_devices(sub.remnawave_uuid)
    except Exception as exc:
        raise HTTPException(502, "panel temporarily unavailable") from exc
    return {
        "items": [
            {
                "hwid": d.hwid,
                "platform": d.platform,
                "model": d.device_model,
                "created_at": d.created_at,
            }
            for d in devices
        ],
        "device_limit": sub.device_limit,
    }


@router.delete("/devices/{hwid}")
async def delete_device(
    hwid: str,
    user: User = Depends(cabinet_user),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
    if sub is None or sub.remnawave_uuid is None:
        raise HTTPException(400, "no active subscription")
    try:
        await container.remnawave_client.delete_device(sub.remnawave_uuid, hwid[:64])
    except Exception as exc:
        raise HTTPException(502, "panel temporarily unavailable") from exc
    return {"ok": True}
