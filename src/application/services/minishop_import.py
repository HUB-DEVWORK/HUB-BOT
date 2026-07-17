"""Importer: remnawave-minishop (3252a8/remnawave-minishop, PostgreSQL) -> our schema.

minishop is a Python/SQLAlchemy shop. Money is MAJOR units (Float in ``payments.amount``),
paid state is the string ``"succeeded"``, subscription status is an UPPERCASE panel label, and
the panel user uuid lives on both ``users.panel_user_uuid`` and ``subscriptions.panel_user_uuid``
as a plain string. Traffic limits are already bytes. There is no wallet.

Idempotent: users match by telegram_id, subscriptions by remnawave_uuid, transactions by
external_id, promocodes by code — a re-run updates instead of duplicating. Panel users are
adopted (uuid kept), so subscribers keep working mid-migration.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid as uuid_mod
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from src.application.services.ids import generate_referral_code, generate_short_id
from src.application.services.pgdump import looks_like_pgdump, parse_copy_blocks
from src.core.enums import (
    Currency,
    Locale,
    PaymentGatewayType,
    SubscriptionStatus,
    TransactionStatus,
    TransactionType,
    UserStatus,
)
from src.infrastructure.database.models.promocode import Promocode
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from pathlib import Path

    from src.application.services.referral import ReferralService
    from src.infrastructure.database.uow import UnitOfWork

SOURCE_TABLES: frozenset[str] = frozenset({"users", "subscriptions", "payments", "promo_codes"})

_SUB_STATUSES = frozenset({"TRIAL", "ACTIVE", "LIMITED", "EXPIRED", "DISABLED", "PENDING"})
# minishop's `provider` string -> our gateway enum (unknowns keep the raw name for display only).
_GATEWAYS = {
    "yookassa": PaymentGatewayType.YOOKASSA,
    "telegram_stars": PaymentGatewayType.TELEGRAM_STARS,
    "stars": PaymentGatewayType.TELEGRAM_STARS,
    "cryptobot": PaymentGatewayType.CRYPTOBOT,
    "cryptomus": PaymentGatewayType.CRYPTOMUS,
    "heleket": PaymentGatewayType.HELEKET,
}


def _to_int(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None if raw is None else int(raw)
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _to_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in {"t", "true", "1", "yes"}


def _to_utc(raw: object) -> dt.datetime | None:
    if raw is None or raw == "":
        return None
    value = raw if isinstance(raw, dt.datetime) else None
    if value is None:
        try:
            value = dt.datetime.fromisoformat(str(raw).strip())
        except ValueError:
            return None
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _to_decimal(raw: object) -> Decimal | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return Decimal(str(raw))
    except ArithmeticError:
        return None


def read_source(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read a minishop dump (pg_dump plain COPY, or a hand {"table": [rows]} JSON) into dicts."""
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        loaded: Any = json.loads(stripped)
        if not isinstance(loaded, dict):
            raise ValueError("это не дамп minishop")
        parsed = {
            str(table): [row for row in rows if isinstance(row, dict)]
            for table, rows in loaded.items()
            if isinstance(rows, list)
        }
    elif looks_like_pgdump(text):
        parsed = dict(parse_copy_blocks(text, set(SOURCE_TABLES)))
    else:
        raise ValueError("это не .sql дамп minishop")
    return {table: parsed.get(table, []) for table in SOURCE_TABLES}


async def read_source_dsn(dsn: str) -> dict[str, list[dict[str, Any]]]:
    """Same shape straight from a live minishop Postgres."""
    import asyncpg  # type: ignore[import-untyped]

    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        data: dict[str, list[dict[str, Any]]] = {}
        for table in sorted(SOURCE_TABLES):
            try:
                records = await conn.fetch(f'SELECT * FROM "{table}"')
            except asyncpg.PostgresError:
                data[table] = []
            else:
                data[table] = [dict(r) for r in records]
        return data
    finally:
        await conn.close()


def probe(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    if not data.get("users"):
        return {"ok": False, "detail": "таблица users пуста или это не дамп minishop"}
    paid = [p for p in data.get("payments", []) if str(p.get("status")).lower() == "succeeded"]
    return {
        "ok": True,
        "counts": {
            "users": len(data["users"]),
            "subscriptions": len(data.get("subscriptions", [])),
            "paid_payments": len(paid),
            "promocodes": len(data.get("promo_codes", [])),
        },
    }


class MinishopImportService:
    def __init__(self, referrals: ReferralService) -> None:
        self._referrals = referrals

    async def run(self, uow: UnitOfWork, data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "users_created": 0,
            "users_updated": 0,
            "referrals_linked": 0,
            "subscriptions": 0,
            "transactions": 0,
            "promocodes": 0,
            "skipped": [],
        }
        by_uid = await self._import_users(uow, data["users"], summary)
        await self._link_referrals(uow, data["users"], by_uid, summary)
        await self._import_subscriptions(uow, data["subscriptions"], by_uid, summary)
        await self._import_payments(uow, data["payments"], by_uid, summary)
        await self._import_promocodes(uow, data["promo_codes"], summary)
        return summary

    @staticmethod
    async def _referral_code(uow: UnitOfWork, raw: object) -> str:
        code = str(raw or "").strip()
        if 1 <= len(code) <= 16 and await uow.users.find_one(referral_code=code) is None:
            return code
        return generate_referral_code()

    async def _import_users(
        self, uow: UnitOfWork, rows: list[dict[str, Any]], summary: dict[str, Any]
    ) -> dict[int, User]:
        """Keyed by the source ``user_id`` (the PK and FK target everywhere in minishop)."""
        by_uid: dict[int, User] = {}
        for row in rows:
            uid = _to_int(row.get("user_id"))
            if uid is None:
                continue
            # For TG users user_id IS the telegram id; a separate telegram_id column may be set.
            tid = _to_int(row.get("telegram_id")) or uid
            if tid is None:
                summary["skipped"].append(f"юзер #{uid}: web-аккаунт без Telegram")
                continue
            user = await uow.users.find_one(telegram_id=tid)
            if user is None:
                user = User(
                    telegram_id=tid,
                    referral_code=await self._referral_code(uow, row.get("referral_code")),
                    currency=Currency.RUB,
                )
                await uow.users.add(user)
                reg = _to_utc(row.get("registration_date"))
                if reg is not None:
                    user.created_at = reg
                summary["users_created"] += 1
            else:
                summary["users_updated"] += 1
            user.username = str(row.get("username") or "")[:64] or None
            user.first_name = str(row.get("first_name") or "")[:128] or None
            user.last_name = str(row.get("last_name") or "")[:128] or None
            language = str(row.get("language_code") or "").strip().lower()[:2]
            user.language = Locale(language) if language in set(Locale) else Locale.default()
            if _to_bool(row.get("is_banned")):
                user.status = UserStatus.BLOCKED
            # Trial spent if they've claimed the welcome bonus or have a reset marker.
            user.is_trial_available = not row.get("trial_eligibility_reset_at")
            by_uid[uid] = user
        await uow.session.flush()
        return by_uid

    async def _link_referrals(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_uid: dict[int, User],
        summary: dict[str, Any],
    ) -> None:
        """minishop keeps the relation on ``users.referred_by_id`` (self-FK to user_id)."""
        for row in rows:
            referred = by_uid.get(_to_int(row.get("user_id")) or 0)
            referrer = by_uid.get(_to_int(row.get("referred_by_id")) or 0)
            if referred is None or referrer is None or referred.referred_by_id is not None:
                continue
            if await self._referrals.bind(uow, referred, referrer.referral_code) is not None:
                summary["referrals_linked"] += 1

    async def _import_subscriptions(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_uid: dict[int, User],
        summary: dict[str, Any],
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        # Several sub rows can share one panel user (renewals) — importing them all would collide
        # on remnawave_uuid. Keep the latest end_date per panel uuid.
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            panel = str(row.get("panel_user_uuid") or "").strip()
            if not panel:
                continue
            best = latest.get(panel)
            if best is None or (_to_utc(row.get("end_date")) or now) >= (
                _to_utc(best.get("end_date")) or now
            ):
                latest[panel] = row

        best_sub: dict[int, Subscription] = {}
        for panel, row in latest.items():
            user = by_uid.get(_to_int(row.get("user_id")) or 0)
            if user is None:
                continue
            try:
                panel_uuid = uuid_mod.UUID(panel)
            except ValueError:
                summary["skipped"].append(f"подписка {panel[:8]}: некорректный uuid панели")
                continue
            expire = _to_utc(row.get("end_date"))
            status_name = str(row.get("status_from_panel") or "").strip().upper()
            status = (
                SubscriptionStatus[status_name]
                if status_name in _SUB_STATUSES
                else (
                    SubscriptionStatus.ACTIVE
                    if _to_bool(row.get("is_active"))
                    else SubscriptionStatus.EXPIRED
                )
            )
            if status.is_usable and expire is not None and expire <= now:
                status = SubscriptionStatus.EXPIRED

            sub = await uow.subscriptions.find_one(remnawave_uuid=panel_uuid)
            if sub is None:
                short = str(row.get("panel_subscription_uuid") or "")[:16] or None
                if short is None or await uow.subscriptions.find_one(short_id=short) is not None:
                    short = generate_short_id()
                sub = Subscription(user_id=user.id, remnawave_uuid=panel_uuid, short_id=short)
                await uow.subscriptions.add(sub)
            sub.status = status
            sub.is_trial = status is SubscriptionStatus.TRIAL
            sub.expire_at = expire
            sub.traffic_limit_bytes = _to_int(row.get("traffic_limit_bytes")) or 0  # already bytes
            sub.device_limit = _to_int(row.get("hwid_device_limit"))
            sub.autopay_enabled = _to_bool(row.get("auto_renew_enabled"))
            sub.plan_snapshot = {"name": "Imported", "source": "minishop"}
            summary["subscriptions"] += 1
            if status.is_usable:
                current = best_sub.get(user.id)
                if current is None or (sub.expire_at or now) > (current.expire_at or now):
                    best_sub[user.id] = sub

        await uow.session.flush()
        for user_id, sub in best_sub.items():
            owner = await uow.users.get(user_id)
            if owner is not None and owner.current_subscription_id is None:
                owner.current_subscription_id = sub.id

    async def _import_payments(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_uid: dict[int, User],
        summary: dict[str, Any],
    ) -> None:
        for row in rows:
            if str(row.get("status") or "").strip().lower() != "succeeded":
                continue
            user = by_uid.get(_to_int(row.get("user_id")) or 0)
            if user is None:
                continue
            external = (
                str(row.get("provider_payment_id") or "")
                or str(row.get("yookassa_payment_id") or "")
                or f"minishop-{_to_int(row.get('payment_id'))}"
            )
            if await uow.transactions.find_one(external_id=external) is not None:
                continue
            amount = _to_decimal(row.get("amount"))
            if amount is None:
                summary["skipped"].append(f"платёж {external}: нет суммы")
                continue
            currency_name = str(row.get("currency") or "RUB").strip().upper()
            try:
                currency = Currency[currency_name]
            except KeyError:
                summary["skipped"].append(
                    f"платёж {external}: валюта {currency_name} не поддержана"
                )
                continue
            amount_minor = int(
                (amount * 10**currency.exponent).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            provider = str(row.get("provider") or "").strip().lower()
            created = _to_utc(row.get("created_at")) or dt.datetime.now(dt.UTC)
            txn = Transaction(
                user_id=user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                status=TransactionStatus.COMPLETED,
                amount_minor=amount_minor,
                currency=currency,
                external_id=external,
                gateway_type=_GATEWAYS.get(provider),
                gateway_display_name=(provider or None) and provider[:64],
                completed_at=_to_utc(row.get("updated_at")) or created,
            )
            await uow.transactions.add(txn)
            txn.created_at = created
            summary["transactions"] += 1

    async def _import_promocodes(
        self, uow: UnitOfWork, rows: list[dict[str, Any]], summary: dict[str, Any]
    ) -> None:
        from src.core.enums import RewardType

        for row in rows:
            code = str(row.get("code") or "")[:64]
            if not code:
                continue
            # minishop promo grants bonus days (RewardType.DURATION); discount-only codes carry 0.
            bonus_days = _to_int(row.get("bonus_days")) or 0
            promo = await uow.promocodes.find_one(code=code)
            if promo is None:
                promo = Promocode(
                    code=code, reward_type=RewardType.DURATION, reward_value=bonus_days
                )
                uow.session.add(promo)
            else:
                promo.reward_type, promo.reward_value = RewardType.DURATION, bonus_days
            promo.is_active = _to_bool(row.get("is_active"))
            promo.expires_at = _to_utc(row.get("valid_until"))
            promo.max_activations = _to_int(row.get("max_activations"))
            summary["promocodes"] += 1
