"""Importer: remnawave-telegram-shop (Jolymmiels/remnawave-telegram-shop, Go, PostgreSQL).

This Go shop stores the subscription expiry ON the customer (``customer.expire_at``) and keeps
NO panel-user uuid — only ``customer.subscription_link``. So we resolve the panel uuid from the
LIVE panel by telegram_id (the operator points the new bot at the same Remnawave during
migration); if the panel can't be reached / has no such user, the subscription is imported
without a uuid (a record — the next renewal re-provisions). Money is MAJOR units
(``purchase.amount`` DECIMAL); paid state is the string ``"paid"``; referrals join on
telegram_id (not the internal customer id). There is no wallet.

Idempotent: users match by telegram_id, subscriptions by remnawave_uuid (when resolved) or
short_id, transactions by external_id.
"""

from __future__ import annotations

import datetime as dt
import json
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
)
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from pathlib import Path

    from src.application.common.panel import RemnawaveClient
    from src.application.services.referral import ReferralService
    from src.infrastructure.database.uow import UnitOfWork

SOURCE_TABLES: frozenset[str] = frozenset({"customer", "purchase", "referral"})

# Jolymmiels `purchase.invoice_type` -> our gateway (unknown/plt_* variants keep the raw name).
_GATEWAYS = {
    "crypto": PaymentGatewayType.CRYPTOBOT,
    "yookasa": PaymentGatewayType.YOOKASSA,
    "telegram": PaymentGatewayType.TELEGRAM_STARS,
    "tribute": PaymentGatewayType.TRIBUTE,
    "plt_sbp": PaymentGatewayType.PLATEGA,
    "plt_cards": PaymentGatewayType.PLATEGA,
    "plt_acq": PaymentGatewayType.PLATEGA,
    "plt_crypto": PaymentGatewayType.PLATEGA,
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


def _short_id_from_url(raw: object) -> str | None:
    url = str(raw or "")
    if not url:
        return None
    seg = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1][:16]
    if seg and seg.isascii() and all(c.isalnum() or c in "_-" for c in seg):
        return seg
    return None


def read_source(path: Path) -> dict[str, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        loaded: Any = json.loads(stripped)
        if not isinstance(loaded, dict):
            raise ValueError("это не дамп remnawave-telegram-shop")
        parsed = {
            str(table): [row for row in rows if isinstance(row, dict)]
            for table, rows in loaded.items()
            if isinstance(rows, list)
        }
    elif looks_like_pgdump(text):
        parsed = dict(parse_copy_blocks(text, set(SOURCE_TABLES)))
    else:
        raise ValueError("это не .sql дамп remnawave-telegram-shop")
    return {table: parsed.get(table, []) for table in SOURCE_TABLES}


async def read_source_dsn(dsn: str) -> dict[str, list[dict[str, Any]]]:
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
    if not data.get("customer"):
        return {"ok": False, "detail": "таблица customer пуста или это не дамп этого бота"}
    paid = [p for p in data.get("purchase", []) if str(p.get("status")).lower() == "paid"]
    return {
        "ok": True,
        "counts": {
            "customers": len(data["customer"]),
            "paid_purchases": len(paid),
            "referrals": len(data.get("referral", [])),
        },
    }


class JolymmielsImportService:
    def __init__(self, referrals: ReferralService, panel: RemnawaveClient | None = None) -> None:
        self._referrals = referrals
        self._panel = panel

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
        by_cid, by_tid = await self._import_customers(uow, data["customer"], summary)
        await self._link_referrals(uow, data["referral"], by_tid, summary)
        await self._import_purchases(uow, data["purchase"], by_cid, summary)
        return summary

    async def _resolve_uuid(self, telegram_id: int) -> Any:
        """Best-effort: find the customer's existing panel user by telegram_id."""
        if self._panel is None:
            return None
        try:
            panel_user = await self._panel.get_user_by_telegram_id(telegram_id)
        except Exception:
            return None
        return getattr(panel_user, "uuid", None)

    async def _import_customers(
        self, uow: UnitOfWork, rows: list[dict[str, Any]], summary: dict[str, Any]
    ) -> tuple[dict[int, User], dict[int, User]]:
        now = dt.datetime.now(dt.UTC)
        by_cid: dict[int, User] = {}
        by_tid: dict[int, User] = {}
        for row in rows:
            cid = _to_int(row.get("id"))
            tid = _to_int(row.get("telegram_id"))
            if cid is None or tid is None:
                continue
            user = await uow.users.find_one(telegram_id=tid)
            if user is None:
                user = User(
                    telegram_id=tid, referral_code=generate_referral_code(), currency=Currency.RUB
                )
                await uow.users.add(user)
                created = _to_utc(row.get("created_at"))
                if created is not None:
                    user.created_at = created
                summary["users_created"] += 1
            else:
                summary["users_updated"] += 1
            language = str(row.get("language") or "").strip().lower()[:2]
            user.language = Locale(language) if language in set(Locale) else Locale.default()
            by_cid[cid] = user
            by_tid[tid] = user
            await uow.session.flush()

            # The subscription lives on the customer (expire_at + subscription_link).
            expire = _to_utc(row.get("expire_at"))
            link = str(row.get("subscription_link") or "")
            if expire is None and not link:
                continue
            panel_uuid = await self._resolve_uuid(tid)
            sub = (
                await uow.subscriptions.find_one(remnawave_uuid=panel_uuid)
                if panel_uuid is not None
                else None
            )
            if sub is None:
                short = _short_id_from_url(link)
                if short is None or await uow.subscriptions.find_one(short_id=short) is not None:
                    short = generate_short_id()
                sub = Subscription(user_id=user.id, remnawave_uuid=panel_uuid, short_id=short)
                await uow.subscriptions.add(sub)
            sub.expire_at = expire
            sub.subscription_url = link[:512] or None
            sub.status = (
                SubscriptionStatus.ACTIVE
                if expire is not None and expire > now
                else SubscriptionStatus.EXPIRED
            )
            sub.plan_snapshot = {"name": "Imported", "source": "remnawave-telegram-shop"}
            await uow.session.flush()
            if sub.status.is_usable and user.current_subscription_id is None:
                user.current_subscription_id = sub.id
            summary["subscriptions"] += 1
        await uow.session.flush()
        return by_cid, by_tid

    async def _link_referrals(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_tid: dict[int, User],
        summary: dict[str, Any],
    ) -> None:
        """referrer_id / referee_id are TELEGRAM ids in this schema, not internal customer ids."""
        for row in rows:
            referred = by_tid.get(_to_int(row.get("referee_id")) or 0)
            referrer = by_tid.get(_to_int(row.get("referrer_id")) or 0)
            if referred is None or referrer is None or referred.referred_by_id is not None:
                continue
            if await self._referrals.bind(uow, referred, referrer.referral_code) is not None:
                summary["referrals_linked"] += 1

    async def _import_purchases(
        self,
        uow: UnitOfWork,
        rows: list[dict[str, Any]],
        by_cid: dict[int, User],
        summary: dict[str, Any],
    ) -> None:
        for row in rows:
            if str(row.get("status") or "").strip().lower() != "paid":
                continue
            user = by_cid.get(_to_int(row.get("customer_id")) or 0)
            if user is None:
                continue
            external = f"jolymmiels-{_to_int(row.get('id'))}"
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
            invoice = str(row.get("invoice_type") or "").strip().lower()
            created = _to_utc(row.get("created_at")) or dt.datetime.now(dt.UTC)
            txn = Transaction(
                user_id=user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                status=TransactionStatus.COMPLETED,
                amount_minor=amount_minor,
                currency=currency,
                external_id=external,
                gateway_type=_GATEWAYS.get(invoice),
                gateway_display_name=(invoice or None) and invoice[:64],
                completed_at=_to_utc(row.get("paid_at")) or created,
            )
            await uow.transactions.add(txn)
            txn.created_at = created
            summary["transactions"] += 1
