"""Tariffs are rebuilt from imported subscriptions.

Operator complaint that started this: «Я с ремнашоп переносил. Перенеслись только
пользователи. Тарифы не перенеслись.»
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from src.application.services.minishop_import import MinishopImportService, read_source
from src.application.services.referral import ReferralService
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import RecordingEventBus

FUTURE = (dt.datetime.now(dt.UTC) + dt.timedelta(days=20)).isoformat()
START = (dt.datetime.now(dt.UTC) - dt.timedelta(days=10)).isoformat()


def _dump(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "users": [
                    {"user_id": 501, "telegram_id": 501},
                    {"user_id": 502, "telegram_id": 502},
                ],
                "subscriptions": [
                    {
                        "subscription_id": 1,
                        "user_id": 501,
                        "panel_user_uuid": "6f9619ff-8b86-4d01-b42d-00cf4fc964aa",
                        "end_date": FUTURE,
                        "start_date": START,
                        "status_from_panel": "ACTIVE",
                        "traffic_limit_bytes": 10 * 1024**3,
                        "hwid_device_limit": 3,
                    },
                    {
                        "subscription_id": 2,
                        "user_id": 502,
                        "panel_user_uuid": "6f9619ff-8b86-4d01-b42d-00cf4fc964bb",
                        "end_date": FUTURE,
                        "start_date": START,
                        "status_from_panel": "ACTIVE",
                        "traffic_limit_bytes": 10 * 1024**3,
                        "hwid_device_limit": 3,
                    },
                ],
                "payments": [],
                "promo_codes": [],
            }
        ),
        encoding="utf-8",
    )


async def test_import_creates_tariffs_and_links_subscriptions(
    uow: UnitOfWork, tmp_path: Path
) -> None:
    src = tmp_path / "minishop.json"
    _dump(src)
    svc = MinishopImportService(ReferralService(RecordingEventBus()))

    async with uow:
        summary = await svc.run(uow, read_source(src))
        await uow.commit()

    assert summary["plans"] == 1  # обе подписки одинаковые -> один тариф
    async with uow:
        plans = await uow.plans.list()
        assert len(plans) == 1
        plan = plans[0]
        assert plan.is_active is False  # цену источник не отдал -> не продаём вслепую
        assert plan.device_limit == 3
        assert plan.traffic_limit_bytes == 10 * 1024**3
        full = await uow.plans.list_with_durations()
        days = [d.days for p in full if p.id == plan.id for d in p.durations]
        assert days == [30]  # фактический период подписки
        subs = await uow.subscriptions.list()
        assert subs and all(s.plan_id == plan.id for s in subs)  # продление пойдёт по тарифу

    async with uow:  # повторный импорт не плодит тарифы
        summary2 = await svc.run(uow, read_source(src))
        await uow.commit()
    assert summary2["plans"] == 0
    async with uow:
        assert len(await uow.plans.list()) == 1
