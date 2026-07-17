"""remnawave-minishop importer: synthetic dump -> our schema, idempotently."""

from __future__ import annotations

import datetime as dt
import json
import uuid as uuid_mod
from pathlib import Path

from src.application.services.minishop_import import MinishopImportService, probe, read_source
from src.application.services.referral import ReferralService
from src.core.enums import SubscriptionStatus, TransactionStatus
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import RecordingEventBus

PANEL_UUID = "6f9619ff-8b86-4d01-b42d-00cf4fc964ff"
FUTURE = (dt.datetime.now(dt.UTC) + dt.timedelta(days=20)).isoformat()


def _dump(path: Path) -> None:
    data = {
        "users": [
            {
                "user_id": 100,
                "telegram_id": 100,
                "username": "alice",
                "first_name": "Alice",
                "referral_code": "ALICE",
                "referred_by_id": None,
                "language_code": "ru",
            },
            {"user_id": 200, "telegram_id": 200, "first_name": "Bob", "referred_by_id": 100},
        ],
        "subscriptions": [
            {
                "subscription_id": 1,
                "user_id": 100,
                "panel_user_uuid": PANEL_UUID,
                "panel_subscription_uuid": "abc123",
                "end_date": FUTURE,
                "status_from_panel": "ACTIVE",
                "traffic_limit_bytes": 10 * 1024**3,
                "is_active": True,
                "auto_renew_enabled": True,
            },
        ],
        "payments": [
            {
                "payment_id": 1,
                "user_id": 100,
                "provider_payment_id": "pay1",
                "provider": "yookassa",
                "amount": 199.0,
                "currency": "RUB",
                "status": "succeeded",
            },
            {
                "payment_id": 2,
                "user_id": 100,
                "provider": "yookassa",
                "amount": 50.0,
                "currency": "RUB",
                "status": "pending",
            },  # not paid -> skipped
        ],
        "promo_codes": [{"code": "WELCOME", "bonus_days": 7, "is_active": True}],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


async def test_minishop_import_maps_and_is_idempotent(uow: UnitOfWork, tmp_path: Path) -> None:
    src = tmp_path / "minishop.json"
    _dump(src)
    data = read_source(src)
    assert probe(data)["ok"]

    svc = MinishopImportService(ReferralService(RecordingEventBus()))
    async with uow:
        summary = await svc.run(uow, data)
        await uow.commit()

    assert summary["users_created"] == 2
    assert summary["subscriptions"] == 1
    assert summary["transactions"] == 1  # pending payment skipped
    assert summary["referrals_linked"] == 1
    assert summary["promocodes"] == 1

    async with uow:
        alice = await uow.users.get_by_telegram_id(100)
        bob = await uow.users.get_by_telegram_id(200)
        assert alice is not None and bob is not None
        assert bob.referred_by_id == alice.id
        sub = await uow.subscriptions.find_one(remnawave_uuid=uuid_mod.UUID(PANEL_UUID))
        assert sub is not None
        assert sub.status is SubscriptionStatus.ACTIVE
        assert sub.traffic_limit_bytes == 10 * 1024**3  # already bytes, not re-multiplied
        assert alice.current_subscription_id == sub.id
        txns = await uow.transactions.list(user_id=alice.id)
        assert len(txns) == 1
        assert txns[0].status is TransactionStatus.COMPLETED
        assert txns[0].amount_minor == 19900  # 199.00 RUB -> minor

    async with uow:  # re-run: no duplicates
        summary2 = await svc.run(uow, data)
        await uow.commit()
    assert summary2["users_created"] == 0
    async with uow:
        assert await uow.users.count(telegram_id=100) == 1
        assert (
            len(await uow.transactions.list(user_id=(await uow.users.get_by_telegram_id(100)).id))
            == 1
        )  # type: ignore[union-attr]
