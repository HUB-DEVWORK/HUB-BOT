"""Idempotent demo seed so the shop works out of the box.

Creates one sellable plan (30 days, RUB price) if no non-trial plan exists, and activates the
``manual`` + ``telegram_stars`` gateways. Safe to re-run — never overwrites admin-created data.

Run: ``uv run python scripts/seed_demo.py``
"""

from __future__ import annotations

import asyncio

from src.core.enums import Currency, PaymentGatewayType
from src.core.logging import configure_logging, get_logger
from src.infrastructure.database.models.payment_gateway import PaymentGateway
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice
from src.infrastructure.di import AppContainer

log = get_logger("seed")


async def main() -> int:
    configure_logging(level="INFO", json=False)
    container = AppContainer.from_env()
    try:
        async with container.uow() as uow:
            sellable = [p for p in await uow.plans.list() if not p.is_trial]
            if not sellable:
                plan = Plan(
                    public_code="demo",
                    name="VPN · 30 дней",
                    description="Демо-тариф",
                    is_active=True,
                    is_trial=False,
                    traffic_limit_bytes=0,  # unlimited
                    device_limit=3,
                    internal_squads=[],
                )
                await uow.plans.add(plan)
                duration = PlanDuration(plan_id=plan.id, days=30)
                uow.session.add(duration)
                await uow.flush()
                uow.session.add(
                    PlanPrice(
                        plan_duration_id=duration.id, currency=Currency.RUB, price_minor=15000
                    )
                )
                log.info("seeded demo plan", price_rub=150)

            for gateway_type in (PaymentGatewayType.MANUAL, PaymentGatewayType.TELEGRAM_STARS):
                gateway = await uow.payment_gateways.find_one(type=gateway_type)
                if gateway is None:
                    uow.session.add(
                        PaymentGateway(type=gateway_type, is_active=True, currency=Currency.RUB)
                    )
                    log.info("activated gateway", gateway=gateway_type.value)
                elif not gateway.is_active:
                    gateway.is_active = True
                    log.info("enabled gateway", gateway=gateway_type.value)

            await uow.commit()
    finally:
        await container.aclose()
    log.info("seed done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
