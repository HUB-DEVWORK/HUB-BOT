"""SoloBot importer: users (balance ₽->kopeks), keys->subscriptions (client_id uuid, ms epoch,
remnawave_link), payments (status="success"), referrals (telegram ids), coupons->promocodes.
Idempotent on re-run."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

from src.application.services import solobot_import as si
from src.application.services.referral import ReferralService
from src.core.enums import RewardType, SubscriptionStatus, TransactionStatus
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import RecordingEventBus

_PUUID = uuid.uuid4()
_EXP_MS = int((dt.datetime.now(dt.UTC) + dt.timedelta(days=30)).timestamp() * 1000)


def test_epoch_ms_and_seconds() -> None:
    got = si._epoch_to_utc(1_800_000_000_000)  # ~2027 in ms
    assert got is not None and got.year == 2027
    assert si._epoch_to_utc(1_800_000_000) == dt.datetime.fromtimestamp(1_800_000_000, tz=dt.UTC)
    assert si._epoch_to_utc(0) is None and si._epoch_to_utc(None) is None


def test_probe() -> None:
    ok = si.probe(
        {
            "users": [{"tg_id": 1}],
            "keys": [{"client_id": "x", "expiry_time": 1}],
            "payments": [{"status": "success"}],
        }
    )
    assert ok["ok"] and ok["counts"]["paid_payments"] == 1
    assert si.probe({"users": []})["ok"] is False
    assert si.probe({"users": [{"tg_id": 1}], "keys": [{"foo": 1}]})["ok"] is False


def _dump(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "users": [
                    {"tg_id": 100, "first_name": "Иван", "language_code": "ru", "balance": 149.5},
                    {"tg_id": 200, "first_name": "Bob", "language_code": "en", "balance": 0},
                ],
                "keys": [
                    {
                        "tg_id": 100,
                        "client_id": str(_PUUID),
                        "expiry_time": _EXP_MS,
                        "remnawave_link": "https://sub.example/s/ABC123",
                        "selected_device_limit": 3,
                        "tariff_id": 5,
                        "is_frozen": False,
                    }
                ],
                "payments": [
                    {
                        "id": 1,
                        "tg_id": 100,
                        "amount": 149.0,
                        "payment_system": "yookassa",
                        "status": "success",
                        "currency": "RUB",
                        "payment_id": "pay-1",
                    },
                    {"id": 2, "tg_id": 100, "amount": 50, "status": "pending"},
                ],
                "referrals": [{"referrer_tg_id": 100, "referred_tg_id": 200}],
                "coupons": [
                    {"code": "WELCOME", "amount": 100, "usage_limit": 5, "percent": 0},
                    {"code": "PCT", "amount": 0, "percent": 10},
                ],
                "tariffs": [{"id": 5, "name": "Год · 3 устройства"}],
            }
        ),
        encoding="utf-8",
    )


async def test_solobot_import(uow: UnitOfWork, tmp_path: Path) -> None:
    src = tmp_path / "solo.json"
    _dump(src)
    data = si.read_source(src)
    assert si.probe(data)["ok"]

    svc = si.SolobotImportService(ReferralService(RecordingEventBus()), panel=None)
    async with uow:
        summary = await svc.run(uow, data)
        await uow.commit()

    assert summary["users_created"] == 2
    assert summary["subscriptions"] == 1
    assert summary["transactions"] == 1  # pending payment skipped
    assert summary["referrals_linked"] == 1
    assert summary["promocodes"] == 1  # percent coupon skipped

    async with uow:
        u1 = await uow.users.get_by_telegram_id(100)
        u2 = await uow.users.get_by_telegram_id(200)
        assert u1 is not None and u2 is not None
        assert u1.balance_minor == 14950  # 149.5 ₽ -> kopeks
        assert u2.referred_by_id == u1.id  # referral join on telegram_id
        sub = await uow.subscriptions.get(u1.current_subscription_id)  # type: ignore[arg-type]
        assert sub is not None
        assert sub.remnawave_uuid == _PUUID  # client_id adopted as panel uuid
        assert sub.short_id == "ABC123"  # from remnawave_link tail
        assert sub.device_limit == 3
        assert sub.status is SubscriptionStatus.ACTIVE
        assert (sub.plan_snapshot or {}).get("name") == "Год · 3 устройства"
        txns = await uow.transactions.list(user_id=u1.id)
        assert len(txns) == 1 and txns[0].status is TransactionStatus.COMPLETED
        assert txns[0].amount_minor == 14900
        promo = await uow.promocodes.find_one(code="WELCOME")
        assert promo is not None and promo.reward_type is RewardType.BALANCE
        assert promo.reward_value == 10000  # 100 ₽ -> kopeks

    async with uow:  # re-run: idempotent
        summary2 = await svc.run(uow, data)
        await uow.commit()
    assert summary2["users_created"] == 0 and summary2["transactions"] == 0
    assert summary2["promocodes"] == 0
    async with uow:
        assert await uow.users.count(telegram_id=100) == 1
