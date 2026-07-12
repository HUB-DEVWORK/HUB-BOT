"""RemnaShop importer: synthetic pg_dump -> our schema, idempotently."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.services.referral import ReferralService
from src.application.services.remnashop_import import (
    RemnashopImportService,
    probe,
    read_source,
)
from src.core.enums import (
    Availability,
    Currency,
    Locale,
    PaymentGatewayType,
    PurchaseType,
    RewardType,
    SubscriptionStatus,
    TransactionType,
    UserStatus,
)
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import RecordingEventBus

PANEL_UUID = "6f9619ff-8b86-4d01-b42d-00cf4fc964ff"
SQUAD_1 = "0a1b2c3d-0000-0000-0000-000000000001"
SQUAD_2 = "0a1b2c3d-0000-0000-0000-000000000002"
XTR_PAYMENT = "aaaaaaaa-1111-2222-3333-444444444444"
RUB_PAYMENT = "bbbbbbbb-1111-2222-3333-444444444444"
PENDING_PAYMENT = "cccccccc-1111-2222-3333-444444444444"
VALUTIX_PAYMENT = "dddddddd-1111-2222-3333-444444444444"

TS = "2025-06-01 10:00:00+00"


def _copy(table: str, cols: list[str], rows: list[list[str]]) -> str:
    head = f"COPY public.{table} ({', '.join(cols)}) FROM stdin;"
    body = "\n".join("\t".join(row) for row in rows)
    return f"{head}\n{body}\n\\."


def _make_dump(path: Path) -> None:
    blocks = [
        "--\n-- PostgreSQL database dump\n--",
        _copy(
            "users",
            [
                "id",
                "telegram_id",
                "username",
                "name",
                "language",
                "referral_code",
                "personal_discount",
                "purchase_discount",
                "points",
                "is_blocked",
                "is_bot_blocked",
                "is_rules_accepted",
                "is_trial_available",
                "current_subscription_id",
                "created_at",
                "updated_at",
            ],
            [
                # referrer; short referral code gets adopted; trial used
                [
                    "1",
                    "1001",
                    "alice",
                    "Alice A",
                    "ru",
                    "alicecode",
                    "15",
                    "0",
                    "0",
                    "f",
                    "f",
                    "t",
                    "f",
                    "11",
                    TS,
                    TS,
                ],
                # referred; 64-char source code -> regenerated
                [
                    "2",
                    "1002",
                    "bob",
                    "Боб",
                    "en",
                    "b" * 64,
                    "0",
                    "10",
                    "5",
                    "f",
                    "f",
                    "f",
                    "t",
                    "\\N",
                    TS,
                    TS,
                ],
                # web-only account -> skipped
                [
                    "3",
                    "\\N",
                    "\\N",
                    "Charlie Web",
                    "en",
                    "webcode123",
                    "0",
                    "0",
                    "0",
                    "f",
                    "f",
                    "f",
                    "t",
                    "\\N",
                    TS,
                    TS,
                ],
                # blocked; unknown locale -> default; discount clamped to 100
                [
                    "4",
                    "1004",
                    "dave",
                    "Dave",
                    "de",
                    "davecode",
                    "150",
                    "0",
                    "0",
                    "t",
                    "f",
                    "f",
                    "f",
                    "\\N",
                    TS,
                    TS,
                ],
            ],
        ),
        _copy(
            "subscriptions",
            [
                "id",
                "user_remna_id",
                "user_id",
                "status",
                "is_trial",
                "disabled_by_channel_leave",
                "traffic_limit",
                "device_limit",
                "traffic_limit_strategy",
                "tag",
                "internal_squads",
                "external_squad",
                "expire_at",
                "url",
                "plan_snapshot",
                "created_at",
                "updated_at",
            ],
            [
                # historical row (same panel user, not current) -> ignored silently
                [
                    "10",
                    PANEL_UUID,
                    "1",
                    "EXPIRED",
                    "f",
                    "f",
                    "50",
                    "1",
                    "NO_RESET",
                    "\\N",
                    "{" + SQUAD_1 + "}",
                    "\\N",
                    "2025-01-01 00:00:00+00",
                    "https://p.example.com/sub/oldshort111",
                    '{"name": "Old"}',
                    TS,
                    TS,
                ],
                # current: 2099 = unlimited sentinel, imported as is
                [
                    "11",
                    PANEL_UUID,
                    "1",
                    "ACTIVE",
                    "f",
                    "f",
                    "100",
                    "3",
                    "NO_RESET",
                    "pro",
                    "{" + SQUAD_1 + "," + SQUAD_2 + "}",
                    "\\N",
                    "2099-12-31 23:59:59+00",
                    "https://p.example.com/sub/aliceShort1?format=v2ray",
                    '{"id": 5, "name": "Pro", "duration": 30}',
                    TS,
                    TS,
                ],
            ],
        ),
        _copy(
            "transactions",
            [
                "id",
                "payment_id",
                "user_id",
                "status",
                "is_test",
                "purchase_type",
                "gateway_type",
                "gateway_display_name",
                "payment_method",
                "pricing",
                "currency",
                "plan_snapshot",
                "created_at",
                "updated_at",
            ],
            [
                # stars: exponent 0, amount_minor == stars count
                [
                    "1",
                    XTR_PAYMENT,
                    "1",
                    "COMPLETED",
                    "f",
                    "NEW",
                    "TELEGRAM_STARS",
                    "Telegram Stars",
                    "\\N",
                    '{"original_amount": 150, "discount_percent": 0, "final_amount": 150}',
                    "XTR",
                    '{"id": 5, "name": "Pro"}',
                    "2025-06-02 12:00:00+00",
                    "2025-06-02 12:00:05+00",
                ],
                # RUB with string Decimal amounts inside JSONB
                [
                    "2",
                    RUB_PAYMENT,
                    "1",
                    "COMPLETED",
                    "f",
                    "RENEW",
                    "YOOKASSA",
                    "ЮKassa",
                    "bank_card",
                    '{"original_amount": "249.00", "discount_percent": 20,'
                    ' "final_amount": "199.00"}',
                    "RUB",
                    '{"id": 5, "name": "Pro"}',
                    "2025-06-03 12:00:00+00",
                    "2025-06-03 12:00:10+00",
                ],
                [
                    "3",
                    PENDING_PAYMENT,
                    "1",
                    "PENDING",
                    "f",
                    "NEW",
                    "YOOKASSA",
                    "\\N",
                    "\\N",
                    '{"original_amount": 100, "discount_percent": 0, "final_amount": 100}',
                    "RUB",
                    "{}",
                    "2025-06-04 12:00:00+00",
                    "2025-06-04 12:00:00+00",
                ],
                # gateway with no counterpart -> gateway_type None, raw name kept
                [
                    "4",
                    VALUTIX_PAYMENT,
                    "2",
                    "COMPLETED",
                    "f",
                    "NEW",
                    "VALUTIX",
                    "\\N",
                    "\\N",
                    '{"original_amount": 500, "discount_percent": 0, "final_amount": 500}',
                    "RUB",
                    '{"name": "Lite"}',
                    "2025-06-05 12:00:00+00",
                    "2025-06-05 12:00:07+00",
                ],
            ],
        ),
        _copy(
            "promocodes",
            [
                "id",
                "code",
                "is_active",
                "reward_type",
                "reward",
                "plan_snapshot",
                "availability",
                "expires_at",
                "max_activations",
                "is_reusable",
                "created_at",
                "updated_at",
            ],
            [
                ["1", "Promo7", "t", "DURATION", "7", "\\N", "ALL", "\\N", "\\N", "f", TS, TS],
                [
                    "2",
                    "DISC10",
                    "t",
                    "PERSONAL_DISCOUNT",
                    "10",
                    "\\N",
                    "NEW",
                    "2099-01-01 00:00:00+00",
                    "100",
                    "t",
                    TS,
                    TS,
                ],
                ["3", "WEIRD", "t", "POINTS", "5", "\\N", "ALL", "\\N", "\\N", "f", TS, TS],
            ],
        ),
        _copy(
            "referrals",
            ["id", "referrer_id", "referred_id", "level", "created_at", "updated_at"],
            [
                ["1", "1", "2", "FIRST", TS, TS],
                ["2", "1", "4", "SECOND", TS, TS],  # derived in the target -> skipped
            ],
        ),
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def test_read_source_accepts_json_and_rejects_garbage(tmp_path: Path) -> None:
    good = tmp_path / "export.json"
    good.write_text('{"users": [{"id": 1}], "junk": 5}', encoding="utf-8")
    data = read_source(good)
    assert data["users"] == [{"id": 1}]
    assert data["promocodes"] == []

    bad = tmp_path / "dump.sql"
    bad.write_text("INSERT INTO users VALUES (1);", encoding="utf-8")
    with pytest.raises(ValueError, match="remnashop"):
        read_source(bad)


async def test_import_and_idempotent_rerun(uow: UnitOfWork, tmp_path: Path) -> None:
    dump = tmp_path / "db_backup_2026-07-10_00-00-00.sql"
    _make_dump(dump)

    data = read_source(dump)
    assert probe(data) == {
        "ok": True,
        "counts": {
            "users": 4,
            "subscriptions": 2,
            "completed_transactions": 3,
            "promocodes": 3,
        },
    }

    service = RemnashopImportService(ReferralService(RecordingEventBus()))
    async with uow:
        summary = await service.run(uow, data)
        await uow.commit()

    assert summary["users_created"] == 3  # web-only account skipped
    assert summary["referrals_linked"] == 1  # FIRST only; SECOND is derived
    assert summary["subscriptions"] == 1  # only the current row, not history
    assert summary["transactions"] == 3  # pending one skipped
    assert summary["promocodes"] == 2
    assert any("web-аккаунт" in s for s in summary["skipped"])
    assert any("WEIRD" in s for s in summary["skipped"])

    async with uow:
        alice = await uow.users.find_one(telegram_id=1001)
        assert alice is not None
        assert alice.referral_code == "alicecode"  # adopted from the source
        assert alice.first_name == "Alice A"
        assert alice.personal_discount_pct == 15
        assert alice.is_trial_available is False
        bob = await uow.users.find_one(telegram_id=1002)
        assert bob is not None
        assert len(bob.referral_code) <= 16  # 64-char source code regenerated
        assert bob.referred_by_id == alice.id
        assert bob.purchase_discount_pct == 10
        assert (await uow.referrals.get_by_referred(bob.id)) is not None
        dave = await uow.users.find_one(telegram_id=1004)
        assert dave is not None
        assert dave.status is UserStatus.BLOCKED
        assert dave.language is Locale.RU  # unknown "de" -> default
        assert dave.personal_discount_pct == 100  # clamped
        assert len(list(await uow.users.list())) == 3

        sub = await uow.subscriptions.find_one(short_id="aliceShort1")
        assert sub is not None
        assert sub.status is SubscriptionStatus.ACTIVE
        assert sub.expire_at is not None and sub.expire_at.year == 2099  # unlimited as is
        assert sub.traffic_limit_bytes == 100 * 1024**3
        assert sub.device_limit == 3
        assert sub.traffic_limit_strategy == "NO_RESET"
        assert sub.internal_squads == [SQUAD_1, SQUAD_2]
        assert sub.subscription_url == "https://p.example.com/sub/aliceShort1?format=v2ray"
        assert sub.plan_snapshot["name"] == "Pro"
        assert sub.plan_snapshot["source"] == "remnashop"
        assert alice.current_subscription_id == sub.id
        assert len(list(await uow.subscriptions.list())) == 1

        stars = await uow.transactions.find_one(external_id=XTR_PAYMENT)
        assert stars is not None
        assert stars.amount_minor == 150  # XTR exponent 0
        assert stars.currency is Currency.XTR
        assert stars.type is TransactionType.SUBSCRIPTION_PAYMENT
        assert stars.gateway_type is PaymentGatewayType.TELEGRAM_STARS
        rub = await uow.transactions.find_one(external_id=RUB_PAYMENT)
        assert rub is not None
        assert rub.amount_minor == 19900  # string "199.00" -> kopeks
        assert rub.purchase_type is PurchaseType.RENEW
        assert rub.pricing["final_amount"] == 199.0
        assert rub.pricing["original_amount"] == 249.0
        assert rub.completed_at is not None
        assert await uow.transactions.find_one(external_id=PENDING_PAYMENT) is None
        valutix = await uow.transactions.find_one(external_id=VALUTIX_PAYMENT)
        assert valutix is not None
        assert valutix.gateway_type is None
        assert valutix.gateway_display_name == "VALUTIX"
        assert valutix.amount_minor == 50000

        promo7 = await uow.promocodes.find_one(code="Promo7")
        assert promo7 is not None
        assert promo7.reward_type is RewardType.DURATION and promo7.reward_value == 7
        assert await uow.promocodes.find_one(code="PROMO7") is None  # case preserved
        disc = await uow.promocodes.find_one(code="DISC10")
        assert disc is not None
        assert disc.reward_type is RewardType.PERSONAL_DISCOUNT and disc.reward_value == 10
        assert disc.availability is Availability.NEW
        assert disc.max_activations == 100 and disc.is_reusable is True

    # Re-run: nothing duplicates, balance is not overwritten.
    async with uow:
        alice = await uow.users.find_one(telegram_id=1001)
        assert alice is not None
        alice.balance_minor = 555  # simulate post-import spending
        await uow.commit()
    async with uow:
        summary2 = await service.run(uow, read_source(dump))
        await uow.commit()
    assert summary2["users_created"] == 0
    assert summary2["users_updated"] == 3
    assert summary2["referrals_linked"] == 0
    assert summary2["transactions"] == 0
    async with uow:
        alice = await uow.users.find_one(telegram_id=1001)
        assert alice is not None and alice.balance_minor == 555
        assert alice.referral_code == "alicecode"
        assert len(list(await uow.users.list())) == 3
        assert len(list(await uow.subscriptions.list())) == 1
        assert len(list(await uow.transactions.list())) == 3
        assert len(list(await uow.promocodes.list())) == 2


async def test_empty_payment_id_fallback_and_is_test_skip(uow: UnitOfWork, tmp_path: Path) -> None:
    dump = tmp_path / "export.json"
    dump.write_text(
        json.dumps(
            {
                "users": [{"id": 1, "telegram_id": 5001, "referral_code": "u1"}],
                "transactions": [
                    # empty payment_id -> synthetic fallback id, still imported (not dropped)
                    {
                        "id": 77,
                        "payment_id": "",
                        "user_id": 1,
                        "status": "COMPLETED",
                        "is_test": False,
                        "gateway_type": "YOOKASSA",
                        "currency": "RUB",
                        "pricing": {"final_amount": 100},
                    },
                    # is_test sandbox payment -> skipped, must not inflate imported revenue
                    {
                        "id": 78,
                        "payment_id": "test-pay-1",
                        "user_id": 1,
                        "status": "COMPLETED",
                        "is_test": True,
                        "gateway_type": "YOOKASSA",
                        "currency": "RUB",
                        "pricing": {"final_amount": 200},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    service = RemnashopImportService(ReferralService(RecordingEventBus()))
    async with uow:
        summary = await service.run(uow, read_source(dump))
        await uow.commit()

    assert summary["transactions"] == 1  # is_test skipped, empty-payment-id one still imported
    async with uow:
        imported = await uow.transactions.find_one(external_id="remnashop-77")
        assert imported is not None
        assert imported.amount_minor == 10000  # 100 RUB -> kopeks
        assert await uow.transactions.find_one(external_id="test-pay-1") is None

    # Re-run: the synthetic fallback id matches idempotently, nothing duplicates.
    async with uow:
        summary2 = await service.run(uow, read_source(dump))
        await uow.commit()
    assert summary2["transactions"] == 0
    async with uow:
        assert len(list(await uow.transactions.list())) == 1
