"""Admin: transactions + net-profit stats, payment providers config (screen 10)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, String, cast, func, or_, select

from src.core.enums import PaymentGatewayType, TransactionStatus, TransactionType
from src.infrastructure.database.models.payment_gateway import PaymentGateway
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import Page, audit, day_bounds_utc, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter()

_STATUS_FILTERS: dict[str, tuple[TransactionStatus, ...]] = {
    "ok": (TransactionStatus.COMPLETED,),
    "pending": (TransactionStatus.PENDING,),
    "failed": (TransactionStatus.FAILED, TransactionStatus.CANCELED),
    "refund": (TransactionStatus.REFUNDED,),
}

_REVENUE_TYPES = (TransactionType.DEPOSIT, TransactionType.SUBSCRIPTION_PAYMENT)


def _tx_stmt(status: str, q: str) -> Select[Any]:
    stmt = (
        select(Transaction, User)
        .join(User, User.id == Transaction.user_id)
        .where(Transaction.is_test.is_(False))
    )
    if status in _STATUS_FILTERS:
        stmt = stmt.where(Transaction.status.in_(_STATUS_FILTERS[status]))
    if q:
        needle = f"%{q.lstrip('@').lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(func.coalesce(User.username, "")).like(needle),
                func.lower(cast(Transaction.payment_id, String)).like(needle),
            )
        )
    return stmt


@router.get("/payments", response_model=Page)
async def list_payments(
    status: str = Query("all"),
    q: str = Query("", max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    container: AppContainer = Depends(get_container),
) -> Page:
    async with container.uow() as uow:
        stmt = _tx_stmt(status, q)
        total = int(
            await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        rows = (
            await uow.session.execute(
                stmt.order_by(Transaction.id.desc()).limit(limit).offset(offset)
            )
        ).all()
        items = [
            {
                "id": t.id,
                "tx": str(t.payment_id)[:8].upper(),
                "user": f"@{u.username}" if u.username else f"id{u.id}",
                "type": t.type.value,
                "purchase_type": t.purchase_type.value if t.purchase_type else None,
                "amount_minor": t.amount_minor,
                "currency": t.currency.value,
                "gateway": t.gateway_type.value if t.gateway_type else None,
                "status": t.status.value,
                "created_at": iso(t.created_at),
                "completed_at": iso(t.completed_at),
            }
            for t, u in rows
        ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/payments/stats")
async def payment_stats(
    tax: int | None = Query(None, ge=0, le=100),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Turnover / provider fees / tax / net profit for today (screen 10 KPIs)."""
    async with container.uow() as uow:
        if tax is None:
            tax = int(await container.bot_config.value(uow, "TAX_RATE_PERCENT"))
        start, end = day_bounds_utc(0)
        rows = (
            await uow.session.execute(
                select(
                    Transaction.gateway_type,
                    func.coalesce(func.sum(Transaction.amount_minor), 0),
                    func.count(),
                )
                .where(
                    Transaction.status == TransactionStatus.COMPLETED,
                    Transaction.type.in_(_REVENUE_TYPES),
                    Transaction.completed_at >= start,
                    Transaction.completed_at < end,
                    Transaction.is_test.is_(False),
                )
                .group_by(Transaction.gateway_type)
            )
        ).all()
        gateways = {g.type: g for g in await uow.payment_gateways.list()}

    turnover = 0
    fees = 0
    per_provider: list[dict[str, Any]] = []
    for gw_type, amount, count in rows:
        amount = int(amount)
        turnover += amount
        fee_bp = gateways[gw_type].fee_bp if gw_type in gateways else 0
        fee = amount * fee_bp // 10_000
        fees += fee
        per_provider.append(
            {
                "gateway": gw_type.value if gw_type else "unknown",
                "amount_minor": amount,
                "count": count,
                "fee_minor": fee,
            }
        )
    tax_minor = (turnover - fees) * tax // 100
    return {
        "turnover_minor": turnover,
        "fees_minor": fees,
        "tax_percent": tax,
        "tax_minor": tax_minor,
        "net_profit_minor": turnover - fees - tax_minor,
        "providers": sorted(per_provider, key=lambda p: -p["amount_minor"]),
    }


# --- providers ----------------------------------------------------------------

# Catalog: human name, payment methods, config fields the UI should render.
# ``ready`` marks gateways whose charge flow is implemented in the base today;
# the rest are UI-configurable and activate once their drop-in lands.
PROVIDER_META: dict[PaymentGatewayType, dict[str, Any]] = {
    PaymentGatewayType.TELEGRAM_STARS: {
        "title": "Telegram Stars",
        "methods": "XTR-инвойсы в боте и мини-аппе",
        "fields": [],
        "ready": True,
        "emoji": "⭐",
    },
    PaymentGatewayType.MANUAL: {
        "title": "Вручную / баланс",
        "methods": "начисление админом",
        "fields": [],
        "ready": True,
        "emoji": "💼",
    },
    PaymentGatewayType.YOOKASSA: {
        "title": "YooKassa",
        "methods": "карта, СБП, SberPay",
        "fields": ["shop_id", "secret_key"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.CRYPTOBOT: {
        "title": "CryptoBot",
        "methods": "USDT, TON, BTC",
        "fields": ["api_token"],
        "ready": False,
        "emoji": "🤖",
    },
    PaymentGatewayType.CRYPTOMUS: {
        "title": "Cryptomus",
        "methods": "крипта, 15+ монет",
        "fields": ["merchant_id", "api_key"],
        "ready": False,
        "emoji": "🪙",
    },
    PaymentGatewayType.TRIBUTE: {
        "title": "Tribute",
        "methods": "карта, подписки",
        "fields": ["api_key"],
        "ready": False,
        "emoji": "💠",
    },
    PaymentGatewayType.PLATEGA: {
        "title": "Platega",
        "methods": "карта, СБП",
        "fields": ["merchant_id", "secret"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.HELEKET: {
        "title": "Heleket",
        "methods": "крипта",
        "fields": ["merchant_id", "api_key"],
        "ready": False,
        "emoji": "🪙",
    },
    PaymentGatewayType.WATA: {
        "title": "WATA",
        "methods": "карта, СБП",
        "fields": ["api_key"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.FREEKASSA: {
        "title": "Freekassa",
        "methods": "карта, СБП, кошельки",
        "fields": ["shop_id", "api_key", "secret1", "secret2"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.PAYPALYCH: {
        "title": "PayPalych",
        "methods": "карта, СБП",
        "fields": ["api_token", "shop_id"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.CLOUDPAYMENTS: {
        "title": "CloudPayments",
        "methods": "карта",
        "fields": ["public_id", "api_secret"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.MULENPAY: {
        "title": "MulenPay",
        "methods": "карта, СБП",
        "fields": ["api_key", "secret_key", "shop_id"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.KASSA_AI: {
        "title": "Kassa.ai",
        "methods": "карта, СБП",
        "fields": ["shop_id", "api_key"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.RIOPAY: {
        "title": "RioPay",
        "methods": "карта, СБП",
        "fields": ["api_key"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.SEVERPAY: {
        "title": "SeverPay",
        "methods": "карта, СБП",
        "fields": ["api_key", "shop_id"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.PAYPEAR: {
        "title": "PayPear",
        "methods": "карта, СБП",
        "fields": ["api_key"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.AURAPAY: {
        "title": "AuraPay",
        "methods": "карта, СБП",
        "fields": ["api_key", "merchant_id"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.OVERPAY: {
        "title": "Overpay",
        "methods": "карта, крипта",
        "fields": ["api_key", "secret"],
        "ready": False,
        "emoji": "🏦",
    },
    PaymentGatewayType.ROLLYPAY: {
        "title": "RollyPay",
        "methods": "СБП, крипта",
        "fields": ["api_key", "shop_id"],
        "ready": False,
        "emoji": "🏦",
    },
}


# Payment forms each provider can offer + brand color for the UI monogram.
PROVIDER_EXTRAS: dict[PaymentGatewayType, dict[str, Any]] = {
    PaymentGatewayType.TELEGRAM_STARS: {"forms": ["stars"], "brand": "#F7971D"},
    PaymentGatewayType.MANUAL: {"forms": ["balance"], "brand": "#6E6E6E"},
    PaymentGatewayType.YOOKASSA: {"forms": ["card", "sbp"], "brand": "#8B3FFD"},
    PaymentGatewayType.CRYPTOBOT: {"forms": ["crypto"], "brand": "#2AABEE"},
    PaymentGatewayType.CRYPTOMUS: {"forms": ["crypto"], "brand": "#3A86FF"},
    PaymentGatewayType.TRIBUTE: {"forms": ["card"], "brand": "#F5C518"},
    PaymentGatewayType.PLATEGA: {"forms": ["card", "sbp"], "brand": "#00A86B"},
    PaymentGatewayType.HELEKET: {"forms": ["crypto"], "brand": "#7B61FF"},
    PaymentGatewayType.WATA: {"forms": ["card", "sbp"], "brand": "#0FA3B1"},
    PaymentGatewayType.FREEKASSA: {"forms": ["card", "sbp", "wallet"], "brand": "#FF6B00"},
    PaymentGatewayType.PAYPALYCH: {"forms": ["card", "sbp"], "brand": "#4C6FFF"},
    PaymentGatewayType.CLOUDPAYMENTS: {"forms": ["card"], "brand": "#2F9BFF"},
    PaymentGatewayType.MULENPAY: {"forms": ["card", "sbp"], "brand": "#9C27B0"},
    PaymentGatewayType.KASSA_AI: {"forms": ["card", "sbp"], "brand": "#00BFA5"},
    PaymentGatewayType.RIOPAY: {"forms": ["card", "sbp"], "brand": "#E91E63"},
    PaymentGatewayType.SEVERPAY: {"forms": ["card", "sbp"], "brand": "#3F51B5"},
    PaymentGatewayType.PAYPEAR: {"forms": ["card", "sbp"], "brand": "#8BC34A"},
    PaymentGatewayType.AURAPAY: {"forms": ["card", "sbp"], "brand": "#FF9800"},
    PaymentGatewayType.OVERPAY: {"forms": ["card", "crypto"], "brand": "#607D8B"},
    PaymentGatewayType.ROLLYPAY: {"forms": ["sbp", "crypto"], "brand": "#00BCD4"},
}


def _provider_row(g: PaymentGateway | None, gtype: PaymentGatewayType) -> dict[str, Any]:
    # Secrets are never echoed: only which config keys are present.
    meta = PROVIDER_META.get(
        gtype,
        {"title": gtype.value, "methods": "", "fields": [], "ready": False, "emoji": "🏦"},
    )
    return {
        "id": g.id if g else None,
        "type": gtype.value,
        "title": meta["title"],
        "emoji": meta["emoji"],
        "methods": meta["methods"],
        "fields": meta["fields"],
        "ready": meta["ready"],
        "display_name": (g.display_name if g else None) or meta["title"],
        "is_active": g.is_active if g else False,
        "currency": g.currency.value if g else "RUB",
        "fee_bp": g.fee_bp if g else 0,
        "configured_keys": sorted(k for k in (g.settings if g else {}) if k != "enabled_forms"),
        "forms": PROVIDER_EXTRAS.get(gtype, {}).get("forms", []),
        "enabled_forms": (g.settings.get("enabled_forms") if g else None)
        or PROVIDER_EXTRAS.get(gtype, {}).get("forms", []),
        "brand": PROVIDER_EXTRAS.get(gtype, {}).get("brand", "#666"),
    }


@router.get("/providers")
async def list_providers(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        rows = {g.type: g for g in await uow.payment_gateways.list()}
    # One row per known type: configured gateways merged over the catalog.
    items = [_provider_row(rows.get(gtype), gtype) for gtype in PROVIDER_META]
    # Ready + active first, then ready, then the rest alphabetically.
    items.sort(key=lambda p: (not p["is_active"], not p["ready"], p["title"].lower()))
    return {"items": items}


class ProviderIn(BaseModel):
    type: PaymentGatewayType
    display_name: str | None = Field(None, max_length=64)
    is_active: bool | None = None
    fee_bp: int | None = Field(None, ge=0, le=10_000)
    enabled_forms: list[str] | None = None
    # Provider credentials (api key, merchant id, webhook secret...). Merged into
    # existing settings; secret-looking keys are Fernet-encrypted at rest.
    settings: dict[str, str] | None = None


_SECRET_HINTS = ("key", "secret", "token", "password")


@router.post("/providers")
async def upsert_provider(
    body: ProviderIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        gw = await uow.payment_gateways.find_one(type=body.type)
        if gw is None:
            gw = PaymentGateway(type=body.type, display_name=body.display_name)
            await uow.payment_gateways.add(gw)
        if body.display_name is not None:
            gw.display_name = body.display_name
        if body.is_active is not None:
            gw.is_active = body.is_active
        if body.fee_bp is not None:
            gw.fee_bp = body.fee_bp
        if body.enabled_forms is not None:
            allowed = set(PROVIDER_EXTRAS.get(body.type, {}).get("forms", []))
            gw.settings = dict(gw.settings) | {
                "enabled_forms": [f for f in body.enabled_forms if f in allowed]
            }
        if body.settings:
            merged = dict(gw.settings)
            for k, v in body.settings.items():
                if v == "":
                    merged.pop(k, None)
                    continue
                is_secret = any(h in k.lower() for h in _SECRET_HINTS)
                if is_secret and container.secret_box is not None:
                    merged[k] = container.secret_box.encrypt(v)
                else:
                    merged[k] = v
            gw.settings = merged
        await audit(
            uow,
            identity,
            "provider.upsert",
            f"provider:{body.type.value}",
            is_active=gw.is_active,
            fee_bp=gw.fee_bp,
        )
        await uow.commit()
        return _provider_row(gw, gw.type)


@router.post("/providers/{gateway_type}/test")
async def test_provider(
    gateway_type: str, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    """Probe provider API/balance. Manual/Stars need no external API — always ok."""
    try:
        gtype = PaymentGatewayType(gateway_type)
    except ValueError as exc:
        raise HTTPException(404, "unknown provider") from exc
    async with container.uow() as uow:
        gw = await uow.payment_gateways.find_one(type=gtype)
    if gw is None:
        raise HTTPException(404, "provider not configured")
    if gtype in (PaymentGatewayType.MANUAL, PaymentGatewayType.TELEGRAM_STARS):
        return {"ok": True, "balance": None, "detail": "no external API required"}
    # Real balance probes land with each gateway implementation (single-file drop-ins).
    return {"ok": False, "balance": None, "detail": "gateway implementation not installed"}
