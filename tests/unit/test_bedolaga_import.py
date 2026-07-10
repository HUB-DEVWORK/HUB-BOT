"""Bedolaga importer: synthetic bot.db / pg_dump -> our schema, idempotently."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from src.application.services.bedolaga_import import BedolagaImportService, probe, read_source
from src.application.services.referral import ReferralService
from src.core.enums import (
    PaymentGatewayType,
    RewardType,
    SubscriptionStatus,
    TransactionType,
    UserStatus,
)
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import RecordingEventBus

SUB_UUID = "6f9619ff-8b86-4d01-b42d-00cf4fc964ff"  # alice: multi-tariff, uuid on the sub row
USER_UUID = "16fd2706-8baf-433b-82eb-8c7fada847da"  # carol: single-tariff, uuid on the user row


def _make_sqlite(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, telegram_id INTEGER, username TEXT, first_name TEXT,
            last_name TEXT, status TEXT, language TEXT, balance_kopeks INTEGER,
            has_had_paid_subscription BOOLEAN, has_made_first_topup BOOLEAN,
            referred_by_id INTEGER, referral_code TEXT, referral_commission_percent INTEGER,
            remnawave_uuid TEXT, created_at TIMESTAMP
        );
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, is_trial BOOLEAN,
            start_date TIMESTAMP, end_date TIMESTAMP, traffic_limit_gb INTEGER,
            traffic_used_gb REAL, subscription_url TEXT, subscription_crypto_link TEXT,
            device_limit INTEGER, connected_squads TEXT, autopay_enabled BOOLEAN,
            autopay_days_before INTEGER, autopay_period_days INTEGER,
            remnawave_short_uuid TEXT, remnawave_uuid TEXT, remnawave_short_id TEXT,
            tariff_id INTEGER, created_at TIMESTAMP
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY, user_id INTEGER, type TEXT, amount_kopeks INTEGER,
            description TEXT, payment_method TEXT, external_id TEXT, is_completed BOOLEAN,
            created_at TIMESTAMP, completed_at TIMESTAMP
        );
        CREATE TABLE promocodes (
            id INTEGER PRIMARY KEY, code TEXT, type TEXT, balance_bonus_kopeks INTEGER,
            subscription_days INTEGER, max_uses INTEGER, valid_until TIMESTAMP,
            is_active BOOLEAN, first_purchase_only BOOLEAN
        );
        CREATE TABLE tariffs (id INTEGER PRIMARY KEY, name TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # referrer with balance (kopeks 1:1) and an adoptable referral code
            (
                1,
                1001,
                "alice",
                "Alice",
                None,
                "active",
                "ru",
                15050,
                1,
                1,
                None,
                "refAlice01",
                30,
                None,
                "2025-01-15 12:00:00",
            ),
            (
                2,
                1002,
                "bob",
                None,
                None,
                "active",
                "en",
                0,
                0,
                0,
                1,
                "refBob00002",
                None,
                None,
                "2025-02-01 08:00:00",
            ),
            # blocked; panel uuid lives on the user row (single-tariff mode)
            (
                3,
                1003,
                "carol",
                None,
                None,
                "blocked",
                "ru",
                500,
                0,
                0,
                None,
                None,
                None,
                USER_UUID,
                "2025-03-01 09:00:00",
            ),
            # cabinet-only user without telegram_id -> skipped
            (
                4,
                None,
                "cabinet",
                None,
                None,
                "active",
                "ru",
                0,
                0,
                0,
                None,
                "refCab00004",
                None,
                None,
                "2025-04-01 10:00:00",
            ),
            # deleted in the source -> skipped
            (
                5,
                1005,
                "ghost",
                None,
                None,
                "deleted",
                "ru",
                100,
                0,
                0,
                None,
                None,
                None,
                None,
                "2025-05-01 11:00:00",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # alice: multi-tariff row, uuid on the subscription itself
            (
                1,
                1,
                "active",
                0,
                "2025-06-01 00:00:00",
                "2099-12-31 23:59:59",
                100,
                1.5,
                "https://sub.example.com/aaa",
                "happ://crypto/aaa",
                3,
                '["sq-1", "sq-2"]',
                1,
                3,
                30,
                "aliceshortuuid",
                SUB_UUID,
                "abc123",
                1,
                "2025-06-01 00:00:00",
            ),
            # carol: uuid only on users.remnawave_uuid; 'active' but past end_date -> EXPIRED
            (
                2,
                3,
                "active",
                1,
                "2024-01-01 00:00:00",
                "2024-02-01 00:00:00",
                0,
                0.0,
                None,
                None,
                1,
                None,
                0,
                3,
                None,
                "carolshort4567890123",
                None,
                "",
                None,
                "2024-01-01 00:00:00",
            ),
            # alice again, no uuid anywhere -> skipped
            (
                3,
                1,
                "pending",
                0,
                None,
                "2099-01-01 00:00:00",
                0,
                0.0,
                None,
                None,
                1,
                None,
                0,
                3,
                None,
                None,
                None,
                "",
                None,
                "2025-06-02 00:00:00",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                1,
                1,
                "deposit",
                50000,
                "topup",
                "pal24",
                "pal-1",
                1,
                "2025-06-01 10:00:00",
                "2025-06-01 10:00:05",
            ),
            (
                2,
                1,
                "subscription_payment",
                19900,
                "buy",
                "apple_iap",
                None,
                1,
                "2025-06-02 10:00:00",
                None,
            ),
            (
                3,
                2,
                "deposit",
                10000,
                "pending topup",
                "yookassa",
                "yk-1",
                0,
                "2025-06-03 10:00:00",
                None,
            ),
            (4, 1, "failed_refund", 100, "oops", None, "fr-1", 1, "2025-06-04 10:00:00", None),
        ],
    )
    conn.executemany(
        "INSERT INTO promocodes VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "CASH100", "balance", 10000, 0, None, None, 1, 0),
            (2, "WEEK7", "subscription_days", 0, 7, 100, "2099-01-01 00:00:00", 1, 0),
            (3, "TRIAL3", "trial_subscription", 0, 3, 0, None, 1, 1),
            (4, "MINUS20", "discount", 20, 0, None, None, 1, 0),  # percent in the kopeks column
            (5, "VIPGROUP", "promo_group", 0, 0, None, None, 1, 0),
        ],
    )
    conn.execute("INSERT INTO tariffs VALUES (1, 'Pro')")
    # Web-cabinet identity: alice owns the email; bob claims the same one -> skipped.
    conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.execute("ALTER TABLE users ADD COLUMN email_verified BOOLEAN")
    conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    conn.execute(
        "UPDATE users SET email='Alice@Example.com', email_verified=1, "
        "password_hash='scrypt$demo' WHERE id=1"
    )
    conn.execute("UPDATE users SET email='alice@example.com' WHERE id=2")
    conn.commit()
    conn.close()


async def test_import_and_idempotent_rerun(uow: UnitOfWork, tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    _make_sqlite(db)

    data = read_source(db)
    assert probe(data) == {
        "ok": True,
        "counts": {"users": 5, "subscriptions": 3, "paid_transactions": 3, "promocodes": 5},
    }

    service = BedolagaImportService(ReferralService(RecordingEventBus()))
    async with uow:
        summary = await service.run(uow, data)
        await uow.commit()

    assert summary["users_created"] == 3
    assert summary["referrals_linked"] == 1
    assert summary["subscriptions"] == 2
    assert summary["transactions"] == 2
    assert summary["promocodes"] == 4
    assert any("без telegram_id" in s for s in summary["skipped"])
    assert any("юзер #5" in s for s in summary["skipped"])
    assert any("подписка #3" in s for s in summary["skipped"])
    assert any("failed_refund" in s for s in summary["skipped"])
    assert any("VIPGROUP" in s for s in summary["skipped"])

    async with uow:
        alice = await uow.users.find_one(telegram_id=1001)
        assert alice is not None
        assert alice.balance_minor == 15050  # kopeks adopted 1:1, no x100
        assert alice.referral_code == "refAlice01"  # adopted from the source
        assert alice.referral_commission_percent == 30
        assert alice.is_trial_available is False
        assert alice.email == "alice@example.com"  # lowercased on import
        assert alice.email_verified is True and alice.password_hash == "scrypt$demo"

        bob = await uow.users.find_one(telegram_id=1002)
        assert bob is not None and bob.referred_by_id == alice.id
        assert bob.is_trial_available is True  # never paid, no sub rows in the dump
        assert bob.email is None  # alice already owns it
        assert any("уже занят" in s for s in summary["skipped"])
        assert (await uow.referrals.get_by_referred(bob.id)) is not None

        carol = await uow.users.find_one(telegram_id=1003)
        assert carol is not None and carol.status is UserStatus.BLOCKED
        assert carol.is_trial_available is False  # has a subscription row in the dump

        sub = await uow.subscriptions.find_one(short_id="abc123")
        assert sub is not None
        assert sub.remnawave_uuid == uuid.UUID(SUB_UUID)
        assert sub.status is SubscriptionStatus.ACTIVE
        assert sub.traffic_limit_bytes == 100 * 1024**3
        assert sub.internal_squads == ["sq-1", "sq-2"]
        assert sub.plan_snapshot["name"] == "Pro"
        assert alice.current_subscription_id == sub.id

        carol_sub = await uow.subscriptions.find_one(remnawave_uuid=uuid.UUID(USER_UUID))
        assert carol_sub is not None  # uuid adopted from users.remnawave_uuid
        assert carol_sub.status is SubscriptionStatus.EXPIRED  # 'active' but past end_date
        assert carol_sub.short_id == "carolshort456789"  # short_uuid truncated to 16
        assert carol.current_subscription_id is None

        topup = await uow.transactions.find_one(external_id="pal-1")
        assert topup is not None
        assert topup.type is TransactionType.DEPOSIT
        assert topup.amount_minor == 50000
        assert topup.gateway_type is PaymentGatewayType.PAYPALYCH

        apple = await uow.transactions.find_one(external_id="bedolaga-2")
        assert apple is not None
        assert apple.gateway_type is None  # apple_iap has no counterpart
        assert apple.gateway_display_name == "apple_iap"

        cash = await uow.promocodes.find_one(code="CASH100")
        assert cash is not None
        assert cash.reward_type is RewardType.BALANCE and cash.reward_value == 10000
        week = await uow.promocodes.find_one(code="WEEK7")
        assert week is not None
        assert week.reward_type is RewardType.DURATION and week.reward_value == 7
        assert week.max_activations == 100
        trial = await uow.promocodes.find_one(code="TRIAL3")
        assert trial is not None
        assert trial.reward_type is RewardType.DURATION and trial.reward_value == 3
        assert trial.max_activations is None  # 0 -> unlimited
        assert trial.first_purchase_only is True
        disc = await uow.promocodes.find_one(code="MINUS20")
        assert disc is not None
        assert disc.reward_type is RewardType.PURCHASE_DISCOUNT and disc.reward_value == 20

    # Re-run: no duplicates, spent balance survives.
    async with uow:
        alice = await uow.users.find_one(telegram_id=1001)
        assert alice is not None
        alice.balance_minor = 777  # simulate post-import spending
        await uow.commit()
    async with uow:
        summary2 = await service.run(uow, read_source(db))
        await uow.commit()
    assert summary2["users_created"] == 0
    assert summary2["users_updated"] == 3
    assert summary2["transactions"] == 0
    async with uow:
        alice = await uow.users.find_one(telegram_id=1001)
        assert alice is not None and alice.balance_minor == 777
        assert len(list(await uow.subscriptions.list())) == 2
        assert len(list(await uow.transactions.list())) == 2


_PG_DUMP = (
    "--\n"
    "-- PostgreSQL database dump\n"
    "--\n\n"
    "COPY public.users (id, telegram_id, username, status, language, balance_kopeks, "
    "has_had_paid_subscription, has_made_first_topup, referral_code, created_at) FROM stdin;\n"
    "1\t5001\tdumpuser\tactive\tru\t2500\tf\tt\trefDump001\t2025-02-01 10:00:00+00\n"
    "2\t\\N\tcabinet\tactive\tru\t0\tf\tf\t\\N\t2025-02-02 10:00:00+00\n"
    "\\.\n\n"
    "COPY public.transactions (id, user_id, type, amount_kopeks, payment_method, "
    "external_id, is_completed, created_at, completed_at) FROM stdin;\n"
    "1\t1\tdeposit\t2500\tyookassa\tyk-dump-1\tt\t2025-02-03 10:00:00+00\t"
    "2025-02-03 10:00:05+00\n"
    "\\.\n"
)


async def test_pgdump_text_source(uow: UnitOfWork, tmp_path: Path) -> None:
    dump = tmp_path / "database.sql"
    dump.write_text(_PG_DUMP, encoding="utf-8")

    data = read_source(dump)
    assert probe(data)["counts"]["users"] == 2

    service = BedolagaImportService(ReferralService(RecordingEventBus()))
    async with uow:
        summary = await service.run(uow, data)
        await uow.commit()

    assert summary["users_created"] == 1  # the cabinet row has no telegram_id
    assert summary["transactions"] == 1
    async with uow:
        user = await uow.users.find_one(telegram_id=5001)
        assert user is not None
        assert user.balance_minor == 2500  # '2500' string -> int kopeks
        assert user.has_made_first_topup is True  # 't' -> True
        txn = await uow.transactions.find_one(external_id="yk-dump-1")
        assert txn is not None
        assert txn.gateway_type is PaymentGatewayType.YOOKASSA
        assert txn.created_at.year == 2025
