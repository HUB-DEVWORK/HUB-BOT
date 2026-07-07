"""remnawave-shopbot importer: synthetic users.db -> our schema, idempotently."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.application.services.referral import ReferralService
from src.application.services.shopbot_import import ShopbotImportService, probe, read_source
from src.core.enums import RewardType, SubscriptionStatus, TransactionType, UserStatus
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import RecordingEventBus

PANEL_UUID = "6f9619ff-8b86-4d01-b42d-00cf4fc964ff"


def _make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            telegram_id INTEGER PRIMARY KEY, username TEXT, total_spent REAL,
            trial_used BOOLEAN, registration_date TIMESTAMP, is_banned BOOLEAN,
            balance REAL, referred_by INTEGER, referral_balance REAL
        );
        CREATE TABLE vpn_keys (
            key_id INTEGER PRIMARY KEY, user_id INTEGER, host_name TEXT, squad_uuid TEXT,
            remnawave_user_uuid TEXT, short_uuid TEXT, email TEXT, key_email TEXT,
            subscription_url TEXT, expire_at TIMESTAMP, traffic_limit_bytes INTEGER, tag TEXT
        );
        CREATE TABLE transactions (
            transaction_id INTEGER PRIMARY KEY, payment_id TEXT, user_id INTEGER,
            status TEXT, amount_rub REAL, payment_method TEXT, metadata TEXT,
            created_date TIMESTAMP
        );
        CREATE TABLE promo_codes (
            code TEXT PRIMARY KEY, discount_percent REAL, discount_amount REAL,
            promo_type TEXT, reward_value INTEGER, usage_limit_total INTEGER,
            valid_until TIMESTAMP, is_active INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)",
        [
            # referrer; float-tail balance: 100.10000000001 RUB -> 10010 kopeks
            (111, "alice", 500.0, 1, "2025-01-15 12:00:00", 0, 100.10000000001, None, 49.9),
            (222, "bob", 0.0, 0, "2025-03-02 09:30:00", 0, 0.0, 111, 0.0),
            (333, "eve", 0.0, 1, "2025-04-01 00:00:00", 1, 5.0, 999999, 0.0),  # ref -> missing
        ],
    )
    conn.execute(
        "INSERT INTO vpn_keys VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            1,
            111,
            "DE-1",
            "sq-uuid-1",
            PANEL_UUID,
            "abc123short",
            "alice@bot.local",
            "alice@bot.local",
            "https://sub.example.com/abc123short",
            "2099-12-31 23:59:59",
            0,
            "Pro",
        ),
    )
    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                1,
                "yk-pay-1",
                111,
                "paid",
                199.0,
                "YooKassa",
                '{"action": "new", "months": 1}',
                "2025-05-01 10:00:00",
            ),
            (
                2,
                "topup-1",
                111,
                "paid",
                300.0,
                "Telegram Stars",
                '{"action": "top_up"}',
                "2025-05-02 10:00:00",
            ),
            (3, "pend-1", 222, "pending", 100.0, "CryptoBot", "{}", "2025-05-03 10:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO promo_codes VALUES (?,?,?,?,?,?,?,?)",
        [
            ("SALE20", 20.0, None, "discount", 0, 100, None, 1),
            ("PLUS7", None, None, "universal", 7, None, "2099-01-01 00:00:00", 1),
            ("CASH50", None, None, "balance", 50, None, None, 1),
            ("FIX100", None, 100.0, "discount", 0, None, None, 1),  # unsupported -> skipped
        ],
    )
    conn.commit()
    conn.close()


async def test_import_and_idempotent_rerun(uow: UnitOfWork, tmp_path: Path) -> None:
    db = tmp_path / "users.db"
    _make_source(db)

    assert probe(db)["counts"] == {
        "users": 3,
        "vpn_keys": 1,
        "paid_transactions": 2,
        "promo_codes": 4,
    }

    service = ShopbotImportService(ReferralService(RecordingEventBus()))
    data = read_source(db)
    async with uow:
        summary = await service.run(uow, data)
        await uow.commit()

    assert summary["users_created"] == 3
    assert summary["referrals_linked"] == 1  # bob->alice; eve's referrer doesn't exist
    assert summary["subscriptions"] == 1
    assert summary["transactions"] == 2  # pending one skipped
    assert summary["promocodes"] == 3
    assert any("FIX100" in s for s in summary["skipped"])

    async with uow:
        alice = await uow.users.find_one(telegram_id=111)
        assert alice is not None
        assert alice.balance_minor == 10010 + 4990  # main + referral wallet, no float tails
        assert alice.is_trial_available is False
        bob = await uow.users.find_one(telegram_id=222)
        assert bob is not None and bob.referred_by_id == alice.id
        assert (await uow.referrals.get_by_referred(bob.id)) is not None
        eve = await uow.users.find_one(telegram_id=333)
        assert eve is not None and eve.status is UserStatus.BLOCKED

        sub = await uow.subscriptions.find_one(short_id="abc123short")
        assert sub is not None
        assert sub.status is SubscriptionStatus.ACTIVE
        assert sub.subscription_url == "https://sub.example.com/abc123short"
        assert alice.current_subscription_id == sub.id

        txn = await uow.transactions.find_one(external_id="yk-pay-1")
        assert txn is not None
        assert txn.amount_minor == 19900
        assert txn.type is TransactionType.SUBSCRIPTION_PAYMENT
        topup = await uow.transactions.find_one(external_id="topup-1")
        assert topup is not None and topup.type is TransactionType.DEPOSIT

        plus7 = await uow.promocodes.find_one(code="PLUS7")
        assert plus7 is not None
        assert plus7.reward_type is RewardType.DURATION and plus7.reward_value == 7
        cash = await uow.promocodes.find_one(code="CASH50")
        assert cash is not None
        assert cash.reward_type is RewardType.BALANCE and cash.reward_value == 5000

    # Re-run: nothing duplicates, balance is not overwritten.
    async with uow:
        alice = await uow.users.find_one(telegram_id=111)
        assert alice is not None
        alice.balance_minor = 777  # simulate post-import spending
        await uow.commit()
    async with uow:
        summary2 = await service.run(uow, read_source(db))
        await uow.commit()
    assert summary2["users_created"] == 0
    assert summary2["transactions"] == 0
    async with uow:
        alice = await uow.users.find_one(telegram_id=111)
        assert alice is not None and alice.balance_minor == 777
        assert len(list(await uow.subscriptions.list())) == 1
