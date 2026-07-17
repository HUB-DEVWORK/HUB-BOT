"""remnawave-telegram-shop (Jolymmiels) importer: synthetic dump -> our schema, idempotently.

Panel is None here, so the subscription is imported without a resolved uuid (short_id from the
link) — the offline path a self-hoster hits when the panel isn't reachable at import time.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from src.application.services.jolymmiels_import import JolymmielsImportService, probe, read_source
from src.application.services.referral import ReferralService
from src.core.enums import SubscriptionStatus, TransactionStatus
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import RecordingEventBus

FUTURE = (dt.datetime.now(dt.UTC) + dt.timedelta(days=15)).isoformat()


def _dump(path: Path) -> None:
    data = {
        "customer": [
            {
                "id": 1,
                "telegram_id": 100,
                "expire_at": FUTURE,
                "subscription_link": "https://sub.example/abc12345",
                "language": "ru",
            },
            {
                "id": 2,
                "telegram_id": 200,
                "expire_at": FUTURE,
                "subscription_link": "https://sub.example/def67890",
            },
        ],
        "purchase": [
            {
                "id": 1,
                "customer_id": 1,
                "amount": "199.00",
                "currency": "RUB",
                "status": "paid",
                "invoice_type": "yookasa",
                "paid_at": "2025-06-01T10:00:00+00:00",
            },
            {
                "id": 2,
                "customer_id": 1,
                "amount": "99.00",
                "currency": "RUB",
                "status": "pending",
                "invoice_type": "crypto",
            },  # not paid -> skipped
        ],
        "referral": [{"referrer_id": 100, "referee_id": 200, "bonus_granted": True}],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


async def test_jolymmiels_import_maps_and_is_idempotent(uow: UnitOfWork, tmp_path: Path) -> None:
    src = tmp_path / "joly.json"
    _dump(src)
    data = read_source(src)
    assert probe(data)["ok"]

    svc = JolymmielsImportService(ReferralService(RecordingEventBus()), panel=None)
    async with uow:
        summary = await svc.run(uow, data)
        await uow.commit()

    assert summary["users_created"] == 2
    assert summary["subscriptions"] == 2
    assert summary["transactions"] == 1  # pending purchase skipped
    assert summary["referrals_linked"] == 1

    async with uow:
        c1 = await uow.users.get_by_telegram_id(100)
        c2 = await uow.users.get_by_telegram_id(200)
        assert c1 is not None and c2 is not None
        assert c2.referred_by_id == c1.id  # referral join is on telegram_id
        sub = await uow.subscriptions.get(c1.current_subscription_id)  # type: ignore[arg-type]
        assert sub is not None
        assert sub.remnawave_uuid is None  # no panel -> uuid unresolved
        assert sub.short_id == "abc12345"  # parsed from subscription_link tail
        assert sub.status is SubscriptionStatus.ACTIVE
        txns = await uow.transactions.list(user_id=c1.id)
        assert len(txns) == 1
        assert txns[0].status is TransactionStatus.COMPLETED
        assert txns[0].amount_minor == 19900

    async with uow:  # re-run: idempotent
        summary2 = await svc.run(uow, data)
        await uow.commit()
    assert summary2["users_created"] == 0
    async with uow:
        assert await uow.users.count(telegram_id=100) == 1
