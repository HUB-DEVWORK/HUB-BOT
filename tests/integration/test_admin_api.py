"""Admin + cabinet API integration tests: ASGI app over in-memory sqlite + fakes."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.application.services.bot_config import BotConfigService
from src.application.services.panel_sync import PanelSyncService
from src.application.services.payment import PaymentService
from src.application.services.pricing import PricingService
from src.application.services.promo import PromoService
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.config import get_settings
from src.core.security import hash_password
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.events import InProcessEventBus
from src.infrastructure.payments.crypto import SecretBox
from src.infrastructure.payments.factory import GatewayFactory
from src.infrastructure.services.telemetry import TelemetryReporter
from tests.fakes.panel import FakeRemnawaveClient

BOT_TOKEN = "12345:TESTTOKEN"
ADMIN_PASSWORD = "AdminPass123!"


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def ping(self) -> bool:
        return True

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def aclose(self) -> None: ...


class ApiTestContainer:
    """Route-facing surface of AppContainer, wired with fakes + test sqlite."""

    def __init__(self, session_factory: async_sessionmaker, settings: Any) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self.redis = _FakeRedis()
        self.remnawave_client = FakeRemnawaveClient()
        self.gateway_factory = GatewayFactory()
        self.secret_box = SecretBox(settings.app.crypt_key)
        self.event_bus = InProcessEventBus()
        self.remnawave = RemnawaveService(self.remnawave_client)
        self.pricing = PricingService()
        self.subscriptions = SubscriptionService(self.remnawave)
        self.purchase = PurchaseService(self.pricing, self.subscriptions, self.event_bus)
        self.referrals = ReferralService(self.event_bus)
        self.payments = PaymentService(self.purchase, self.event_bus, self.referrals)
        self.promo = PromoService()
        self.bot_config = BotConfigService(self.secret_box)
        self.panel_sync = PanelSyncService(self.remnawave_client)
        self.telemetry = TelemetryReporter(
            enabled=False, url="", app_version="test", install_id="test"
        )

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    async def aclose(self) -> None: ...


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, ApiTestContainer]]:
    monkeypatch.setenv("APP__JWT_SECRET", "test-jwt-secret-for-api")
    monkeypatch.setenv("APP__CRYPT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BOT__TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN__DEMO_ENABLED", "false")  # hermetic: ignore local .env
    get_settings.cache_clear()
    settings = get_settings()

    from src.web.app import create_app

    app = create_app()
    container = ApiTestContainer(session_factory, settings)
    app.state.container = container

    # Seed the admin account (bootstrap normally runs in lifespan).
    from src.application.services.ids import generate_referral_code
    from src.core.enums import AuthType, Role
    from src.infrastructure.database.models.user import User

    async with container.uow() as uow:
        await uow.users.add(
            User(
                username="root_admin",
                auth_type=AuthType.EMAIL,
                role=Role.OWNER,
                referral_code=generate_referral_code(),
                password_hash=hash_password(ADMIN_PASSWORD),
            )
        )
        await uow.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, container
    get_settings.cache_clear()


async def _login(http: httpx.AsyncClient) -> dict[str, str]:
    res = await http.post(
        "/api/admin/auth/login",
        json={"username": "root_admin", "password": ADMIN_PASSWORD},
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _tma_headers(tg_id: int = 777000111) -> dict[str, str]:
    user = {"id": tg_id, "first_name": "Тест", "username": "e2e", "language_code": "ru"}
    pairs = {
        "auth_date": str(int(time.time())),
        "query_id": "AAE",
        "user": json.dumps(user, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"tma {urllib.parse.urlencode(pairs)}"}


# --- auth ----------------------------------------------------------------------


async def test_login_and_me(client: tuple[httpx.AsyncClient, ApiTestContainer]) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.get("/api/admin/auth/me", headers=auth)
    assert res.status_code == 200
    assert res.json()["username"] == "root_admin"
    assert res.json()["role"] == "OWNER"


async def test_delete_user_cascades_and_protects_staff(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    """DELETE /users/{id} removes the user + cascades their rows; staff accounts are protected."""
    from src.application.services.ids import generate_referral_code
    from src.core.enums import AuthType, Currency, Role, TransactionStatus, TransactionType
    from src.infrastructure.database.models.transaction import Transaction
    from src.infrastructure.database.models.user import User

    http, container = client
    async with container.uow() as uow:
        buyer = User(
            telegram_id=8080,
            auth_type=AuthType.TELEGRAM,
            referral_code=generate_referral_code(),
        )
        await uow.users.add(buyer)
        await uow.flush()
        await uow.transactions.add(
            Transaction(
                user_id=buyer.id,
                type=TransactionType.DEPOSIT,
                status=TransactionStatus.COMPLETED,
                amount_minor=10000,
                currency=Currency.RUB,
            )
        )
        await uow.commit()
        buyer_id = buyer.id

    auth = await _login(http)
    # staff (the seeded root_admin, OWNER) cannot be deleted
    async with container.uow() as uow:
        staff = await uow.users.find_one(username="root_admin")
        assert staff is not None and staff.role is Role.OWNER
        staff_id = staff.id
    res = await http.delete(f"/api/admin/users/{staff_id}", headers=auth)
    assert res.status_code == 400

    # a normal user is deleted (related rows cascade via ON DELETE CASCADE on the prod Postgres;
    # the in-memory sqlite test DB doesn't enforce FKs, so we assert the user itself is gone).
    res = await http.delete(f"/api/admin/users/{buyer_id}", headers=auth)
    assert res.status_code == 200
    async with container.uow() as uow:
        assert await uow.users.get(buyer_id) is None


async def test_plans_endpoint_survives_malformed_snapshot(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    """The Tariffs screen must not 500 when a completed payment carries a plan_snapshot
    whose plan_id isn't cleanly numeric (regression: an in-SQL CAST aborted the txn)."""
    from src.application.services.ids import generate_referral_code
    from src.core.enums import AuthType, Currency, TransactionStatus, TransactionType
    from src.infrastructure.database.models.transaction import Transaction
    from src.infrastructure.database.models.user import User
    from tests.factories import make_plan

    http, container = client
    async with container.uow() as uow:
        plan, _ = await make_plan(uow, code="tariff-a")
        buyer = User(
            telegram_id=555,
            auth_type=AuthType.TELEGRAM,
            referral_code=generate_referral_code(),
        )
        await uow.users.add(buyer)
        await uow.flush()
        for snap in ({"plan_id": plan.id}, {"plan_id": "oops"}, {"plan_id": None}):
            await uow.transactions.add(
                Transaction(
                    user_id=buyer.id,
                    type=TransactionType.SUBSCRIPTION_PAYMENT,
                    status=TransactionStatus.COMPLETED,
                    amount_minor=30000,
                    currency=Currency.RUB,
                    plan_snapshot=snap,
                )
            )
        await uow.commit()

    auth = await _login(http)
    res = await http.get("/api/admin/plans", headers=auth)
    assert res.status_code == 200
    row = next(p for p in res.json()["items"] if p["id"] == plan.id)
    assert row["sales"] == 1  # only the clean numeric plan_id is counted; poison/None skipped


async def test_bad_password_401(client: tuple[httpx.AsyncClient, ApiTestContainer]) -> None:
    http, _ = client
    res = await http.post(
        "/api/admin/auth/login", json={"username": "root_admin", "password": "wrong"}
    )
    assert res.status_code == 401


async def test_protected_requires_token(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    assert (await http.get("/api/admin/dashboard")).status_code == 401
    assert (
        await http.get("/api/admin/dashboard", headers={"Authorization": "Bearer junk"})
    ).status_code == 401


# --- settings hot-reload ---------------------------------------------------------


async def test_settings_patch_and_search(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.patch(
        "/api/admin/settings",
        headers=auth,
        json={"changes": {"TRIAL_DURATION_DAYS": 9, "NALOGO_TOKEN": "sss"}},
    )
    assert res.status_code == 200
    assert set(res.json()["applied"]) == {"TRIAL_DURATION_DAYS", "NALOGO_TOKEN"}

    res = await http.get("/api/admin/settings", headers=auth, params={"q": "TRIAL_DURATION"})
    row = res.json()["params"][0]
    assert row["value"] == 9
    assert row["is_overridden"] is True

    res = await http.get("/api/admin/settings", headers=auth, params={"q": "NALOGO_TOKEN"})
    assert res.json()["params"][0]["value"] == "••••••••"

    res = await http.patch("/api/admin/settings", headers=auth, json={"changes": {"BOGUS_KEY": 1}})
    assert res.status_code == 400


# --- menu constructor -------------------------------------------------------------


async def test_menu_save_and_cycle_guard(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    nodes = [
        {"id": "root1", "label": "Купить", "kind": "action", "payload": "buy"},
        {"id": "scr", "label": "Инфо", "kind": "screen", "payload": "Текст"},
        {"id": "child", "parent": "scr", "label": "FAQ", "kind": "link", "payload": "https://x"},
    ]
    res = await http.put("/api/admin/bot-menu", headers=auth, json={"nodes": nodes})
    assert res.status_code == 200
    saved = res.json()["nodes"]
    assert len(saved) == 3
    child = next(n for n in saved if n["label"] == "FAQ")
    parent = next(n for n in saved if n["label"] == "Инфо")
    assert child["parent"] == parent["id"]

    # cycle: a->b->a
    bad = [
        {"id": "a", "parent": "b", "label": "A", "kind": "screen"},
        {"id": "b", "parent": "a", "label": "B", "kind": "screen"},
    ]
    res = await http.put("/api/admin/bot-menu", headers=auth, json={"nodes": bad})
    assert res.status_code == 400


async def test_menu_row_layout_and_action_validation(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    # The constructor's row-layout control persists row_index/order_index so the bot renders
    # the operator's chosen grid (two buttons on row 0, one on row 1).
    nodes = [
        {
            "id": "a",
            "label": "A",
            "kind": "action",
            "payload": "buy",
            "row_index": 0,
            "order_index": 0,
        },
        {
            "id": "b",
            "label": "B",
            "kind": "action",
            "payload": "balance",
            "row_index": 0,
            "order_index": 1,
        },
        {
            "id": "c",
            "label": "C",
            "kind": "action",
            "payload": "support",
            "row_index": 1,
            "order_index": 0,
        },
    ]
    res = await http.put("/api/admin/bot-menu", headers=auth, json={"nodes": nodes})
    assert res.status_code == 200
    saved = {n["label"]: n for n in res.json()["nodes"]}
    assert saved["A"]["row_index"] == 0 and saved["B"]["row_index"] == 0
    assert saved["C"]["row_index"] == 1

    # A NON-EMPTY unknown code is rejected — it would be a dead button (immediate typo feedback).
    bad = [{"id": "x", "label": "Опечатка", "kind": "action", "payload": "by"}]
    res = await http.put("/api/admin/bot-menu", headers=auth, json={"nodes": bad})
    assert res.status_code == 400

    # But an EMPTY payload (an unconfigured placeholder button, common in menus built under an
    # older version) is allowed — rejecting it would lock the operator out of saving the menu.
    placeholder = [{"id": "p", "label": "Новая кнопка", "kind": "action", "payload": None}]
    res = await http.put("/api/admin/bot-menu", headers=auth, json={"nodes": placeholder})
    assert res.status_code == 200


async def test_deep_link_redirect_wraps_allowed_scheme(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    # A client-app scheme is returned inside an https page that fires it (openable from the
    # external browser, unlike the Telegram WebView which errors on ERR_UNKNOWN_URL_SCHEME).
    res = await http.get("/dl", params={"to": "happ://add/https://sub.example/abc"})
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "happ://add/https://sub.example/abc" in res.text

    # Disallowed schemes (open-redirect / XSS vectors) are rejected, not reflected.
    for bad in ("javascript:alert(1)", "https://evil.example", "data:text/html,x"):
        r = await http.get("/dl", params={"to": bad})
        assert r.status_code == 400, bad


async def test_menu_actions_catalog(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    assert (await http.get("/api/admin/bot-menu/actions")).status_code == 401  # protected
    res = await http.get("/api/admin/bot-menu/actions", headers=auth)
    assert res.status_code == 200
    actions = res.json()["actions"]
    codes = {a["code"] for a in actions}
    assert {"buy", "connect", "support"} <= codes
    buy = next(a for a in actions if a["code"] == "buy")
    assert buy["label_ru"] and "needs_subscription" in buy


async def test_menu_reset_default_seeds_editable_menu(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.post("/api/admin/bot-menu/reset-default", headers=auth)
    assert res.status_code == 200, res.text
    nodes = res.json()["nodes"]
    assert len(nodes) >= 2
    assert all(n["parent"] is None and n["kind"] == "action" for n in nodes)
    # persisted + editable: a follow-up GET returns the same seeded tree
    got = (await http.get("/api/admin/bot-menu", headers=auth)).json()["nodes"]
    assert len(got) == len(nodes)
    assert any("Купить" in n["label"] for n in got)


async def test_bootstrap_menu_seeds_once(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    from src.bot.default_menu import DEFAULT_MENU
    from src.web.routes.admin.menu import bootstrap_menu

    _, container = client
    async with container.uow() as uow:
        assert await uow.menu_nodes.count() == 0  # fresh DB: empty menu
    await bootstrap_menu(container)  # first boot -> seeds the default
    async with container.uow() as uow:
        assert await uow.menu_nodes.count() == len(DEFAULT_MENU)
    await bootstrap_menu(container)  # idempotent: never overwrites an existing menu
    async with container.uow() as uow:
        assert await uow.menu_nodes.count() == len(DEFAULT_MENU)


# --- expiry reminders --------------------------------------------------------------


async def test_reminders_crud(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.post(
        "/api/admin/reminders",
        headers=auth,
        json={"hours_before": 6, "text": "истекает через {time}", "button_enabled": True},
    )
    assert res.status_code == 200, res.text
    rid = res.json()["id"]
    # duplicate offset -> 409
    assert (
        await http.post("/api/admin/reminders", headers=auth, json={"hours_before": 6, "text": "x"})
    ).status_code == 409
    # list is ordered furthest-out first
    await http.post("/api/admin/reminders", headers=auth, json={"hours_before": 1, "text": "y"})
    listed = (await http.get("/api/admin/reminders", headers=auth)).json()["items"]
    hours = [i["hours_before"] for i in listed]
    assert hours == sorted(hours, reverse=True) and 6 in hours and 1 in hours
    # patch + delete
    res = await http.patch(f"/api/admin/reminders/{rid}", headers=auth, json={"enabled": False})
    assert res.status_code == 200 and res.json()["enabled"] is False
    assert (await http.delete(f"/api/admin/reminders/{rid}", headers=auth)).status_code == 200


async def test_bootstrap_reminders_seeds_once(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    from src.web.routes.admin.reminders import DEFAULT_STEPS, bootstrap_reminders

    _, container = client
    async with container.uow() as uow:
        assert await uow.reminders.count() == 0
    await bootstrap_reminders(container)
    async with container.uow() as uow:
        assert await uow.reminders.count() == len(DEFAULT_STEPS)
    await bootstrap_reminders(container)  # idempotent
    async with container.uow() as uow:
        assert await uow.reminders.count() == len(DEFAULT_STEPS)


# --- notification templates ---------------------------------------------------------


async def test_notifications_list_and_patch(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.get("/api/admin/notifications", headers=auth)
    assert res.status_code == 200
    events = {i["event"] for i in res.json()["items"]}
    assert {"purchase", "balance_topup", "trial_started", "refund"} <= events
    res = await http.patch(
        "/api/admin/notifications/purchase",
        headers=auth,
        json={"text": "Готово, {name}!", "enabled": True},
    )
    assert res.status_code == 200 and res.json()["text"] == "Готово, {name}!"
    assert (
        await http.patch("/api/admin/notifications/nope", headers=auth, json={"enabled": False})
    ).status_code == 404


async def test_bootstrap_notifications_additive_and_render(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    from src.web.routes.admin.notifications import (
        NOTIFICATION_EVENTS,
        bootstrap_notifications,
        notification_text,
    )

    _, container = client
    async with container.uow() as uow:
        assert await uow.notifications.count() == 0
    await bootstrap_notifications(container)
    async with container.uow() as uow:
        assert await uow.notifications.count() == len(NOTIFICATION_EVENTS)
        rendered = await notification_text(uow, "balance_topup", amount="777 ₽", balance="1000 ₽")
        assert rendered is not None and "777" in rendered
    await bootstrap_notifications(container)  # idempotent
    async with container.uow() as uow:
        assert await uow.notifications.count() == len(NOTIFICATION_EVENTS)
        # a disabled event yields None so the caller stays silent
        row = await uow.notifications.by_event("purchase")
        assert row is not None
        row.enabled = False
        await uow.commit()
    async with container.uow() as uow:
        assert await notification_text(uow, "purchase") is None


# --- sale campaigns -----------------------------------------------------------------


async def test_sales_crud_and_window_guard(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.post(
        "/api/admin/sales",
        headers=auth,
        json={"discount_pct": 25, "start_day": 1, "end_day": 3, "max_uses": 50},
    )
    assert res.status_code == 200, res.text
    sid = res.json()["id"]
    # start_day > end_day is rejected by validation
    bad = await http.post(
        "/api/admin/sales",
        headers=auth,
        json={"discount_pct": 10, "start_day": 5, "end_day": 2},
    )
    assert bad.status_code == 422
    listed = (await http.get("/api/admin/sales", headers=auth)).json()["items"]
    assert any(s["id"] == sid for s in listed)
    res = await http.patch(
        f"/api/admin/sales/{sid}", headers=auth, json={"enabled": True, "discount_pct": 30}
    )
    assert res.status_code == 200 and res.json()["discount_pct"] == 30
    assert (await http.delete(f"/api/admin/sales/{sid}", headers=auth)).status_code == 200


# --- users actions ------------------------------------------------------------------


async def test_user_balance_action_creates_transaction(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, container = client
    auth = await _login(http)
    # create an end user via the cabinet (auto-upsert)
    res = await http.get("/api/cabinet/me", headers=_tma_headers())
    assert res.status_code == 200, res.text
    user_id = res.json()["user"]["id"]

    res = await http.post(
        f"/api/admin/users/{user_id}/balance", headers=auth, json={"amount_minor": 50000}
    )
    assert res.status_code == 200
    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        assert user is not None and user.balance_minor == 50000
        txs = await uow.transactions.list(user_id=user_id)
        assert len(txs) == 1 and txs[0].amount_minor == 50000

    # blocking staff is refused
    res = await http.post("/api/admin/users/1/block", headers=auth)
    assert res.status_code == 400


# --- cabinet flow ----------------------------------------------------------------------


async def test_cabinet_auth_rejects_bad_signature(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    res = await http.get(
        "/api/cabinet/me", headers={"Authorization": "tma hash=deadbeef&auth_date=1"}
    )
    assert res.status_code == 401


async def test_cabinet_purchase_with_balance(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, container = client
    auth = await _login(http)
    tma = _tma_headers()

    # plan via admin API
    res = await http.post(
        "/api/admin/plans",
        headers=auth,
        json={
            "name": "Test Plan",
            "traffic_limit_gb": 100,
            "device_limit": 3,
            "durations": [{"days": 30, "price_minor": 19900}],
        },
    )
    assert res.status_code == 200
    plan_id = res.json()["id"]

    user_id = (await http.get("/api/cabinet/me", headers=tma)).json()["user"]["id"]
    await http.post(
        f"/api/admin/users/{user_id}/balance", headers=auth, json={"amount_minor": 50000}
    )

    # insufficient -> then ok
    res = await http.post(
        "/api/cabinet/purchase",
        headers=tma,
        json={"plan_id": plan_id, "days": 30, "method": "balance"},
    )
    assert res.status_code == 200, res.text

    me = (await http.get("/api/cabinet/me", headers=tma)).json()
    assert me["subscription"] is not None
    assert me["subscription"]["status"] == "active"
    assert me["user"]["balance_minor"] == 50000 - 19900
    assert container.remnawave_client.created_count() == 1

    # connection endpoint serves the provisioned URL
    res = await http.get("/api/cabinet/connection", headers=tma)
    assert res.status_code == 200
    assert res.json()["subscription_url"]


async def test_cabinet_purchase_insufficient_balance(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, container = client
    auth = await _login(http)
    tma = _tma_headers(tg_id=555000222)
    res = await http.post(
        "/api/admin/plans",
        headers=auth,
        json={"name": "Pricey", "durations": [{"days": 30, "price_minor": 100000}]},
    )
    plan_id = res.json()["id"]
    await http.get("/api/cabinet/me", headers=tma)  # upsert
    res = await http.post(
        "/api/cabinet/purchase",
        headers=tma,
        json={"plan_id": plan_id, "days": 30, "method": "balance"},
    )
    assert res.status_code == 402
    assert container.remnawave_client.created_count() == 0


async def test_cabinet_reset_link(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    tma = _tma_headers(tg_id=666000444)
    res = await http.post(
        "/api/admin/plans",
        headers=auth,
        json={"name": "Reset", "durations": [{"days": 30, "price_minor": 10000}]},
    )
    plan_id = res.json()["id"]
    uid = (await http.get("/api/cabinet/me", headers=tma)).json()["user"]["id"]
    await http.post(f"/api/admin/users/{uid}/balance", headers=auth, json={"amount_minor": 50000})
    res = await http.post(
        "/api/cabinet/purchase",
        headers=tma,
        json={"plan_id": plan_id, "days": 30, "method": "balance"},
    )
    assert res.status_code == 200, res.text
    res = await http.post("/api/cabinet/subscription/reset-link", headers=tma)
    assert res.status_code == 200, res.text
    assert res.json()["subscription_url"]
    # rate-limited on an immediate retry
    assert (await http.post("/api/cabinet/subscription/reset-link", headers=tma)).status_code == 429


async def test_cabinet_traffic(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    tma = _tma_headers(tg_id=777000999)
    # no subscription yet -> zeros + empty series
    res = await http.get("/api/cabinet/traffic", headers=tma)
    assert res.status_code == 200 and res.json()["series"] == []
    # after a purchase -> current usage + limit reported
    r = await http.post(
        "/api/admin/plans",
        headers=auth,
        json={
            "name": "Traf",
            "traffic_limit_gb": 100,
            "durations": [{"days": 30, "price_minor": 10000}],
        },
    )
    plan_id = r.json()["id"]
    uid = (await http.get("/api/cabinet/me", headers=tma)).json()["user"]["id"]
    await http.post(f"/api/admin/users/{uid}/balance", headers=auth, json={"amount_minor": 50000})
    await http.post(
        "/api/cabinet/purchase",
        headers=tma,
        json={"plan_id": plan_id, "days": 30, "method": "balance"},
    )
    body = (await http.get("/api/cabinet/traffic", headers=tma)).json()
    assert "used_bytes" in body
    assert body["limit_bytes"] == 100 * 1024**3


async def test_blacklist_crud(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.post(
        "/api/admin/blacklist", headers=auth, json={"telegram_id": 999, "reason": "spam"}
    )
    assert res.status_code == 200, res.text
    dup = await http.post("/api/admin/blacklist", headers=auth, json={"telegram_id": 999})
    assert dup.status_code == 409
    items = (await http.get("/api/admin/blacklist", headers=auth)).json()["items"]
    assert 999 in [e["telegram_id"] for e in items]
    assert (await http.delete("/api/admin/blacklist/999", headers=auth)).status_code == 200
    assert (await http.delete("/api/admin/blacklist/999", headers=auth)).status_code == 404


# --- servers sync -------------------------------------------------------------------------


async def test_servers_sync_mirrors_nodes(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.post("/api/admin/servers/sync", headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["synced"] >= 1
    assert body["items"][0]["status"] in ("online", "offline", "maintenance")

    # for-sale toggle persists
    node_id = body["items"][0]["id"]
    res = await http.patch(
        f"/api/admin/servers/{node_id}", headers=auth, json={"is_for_sale": False}
    )
    assert res.status_code == 200
    res = await http.get("/api/admin/servers", headers=auth)
    assert res.json()["items"][0]["is_for_sale"] is False


# --- promocodes ------------------------------------------------------------------------------


async def test_promocode_lifecycle(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    auth = await _login(http)
    res = await http.post(
        "/api/admin/promocodes",
        headers=auth,
        json={"reward_type": "balance", "reward_value": 10000, "max_activations": 5},
    )
    assert res.status_code == 200
    promo_id, code = res.json()["id"], res.json()["code"]

    # cabinet user applies it -> balance credited
    tma = _tma_headers(tg_id=444000333)
    await http.get("/api/cabinet/me", headers=tma)
    res = await http.post("/api/cabinet/promocode", headers=tma, json={"code": code})
    assert res.status_code == 200 and res.json()["ok"] is True
    me = (await http.get("/api/cabinet/me", headers=tma)).json()
    assert me["user"]["balance_minor"] == 10000

    # re-apply rejected
    res = await http.post("/api/cabinet/promocode", headers=tma, json={"code": code})
    assert res.json()["ok"] is False

    res = await http.delete(f"/api/admin/promocodes/{promo_id}", headers=auth)
    assert res.status_code == 200


# --- demo mode -------------------------------------------------------------------------------


async def test_demo_login_read_only(
    client: tuple[httpx.AsyncClient, ApiTestContainer], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, container = client
    # off by default -> 404
    assert (await http.post("/api/admin/auth/demo")).status_code == 404
    assert (await http.get("/api/admin/auth/demo")).json() == {"enabled": False}

    monkeypatch.setattr(container.settings.admin, "demo_enabled", True)
    res = await http.post("/api/admin/auth/demo")
    assert res.status_code == 200
    assert res.json()["role"] == "PREVIEW"
    demo_auth = {"Authorization": f"Bearer {res.json()['token']}"}

    # reads work
    assert (await http.get("/api/admin/dashboard", headers=demo_auth)).status_code == 200
    assert (await http.get("/api/admin/settings", headers=demo_auth)).status_code == 200
    # mutations are blocked
    res = await http.patch(
        "/api/admin/settings", headers=demo_auth, json={"changes": {"TRIAL_ENABLED": False}}
    )
    assert res.status_code == 403
    assert (await http.post("/api/admin/servers/sync", headers=demo_auth)).status_code == 403

    # demo cannot log in through the password form
    res = await http.post("/api/admin/auth/login", json={"username": "demo", "password": ""})
    assert res.status_code in (401, 422)

    # switching demo off kills existing demo sessions
    monkeypatch.setattr(container.settings.admin, "demo_enabled", False)
    assert (await http.get("/api/admin/dashboard", headers=demo_auth)).status_code == 401


async def test_bootstrap_public_urls_autowires_bot_miniapp_link(
    client: tuple[httpx.AsyncClient, ApiTestContainer], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.web.routes.admin.maintenance import bootstrap_public_urls

    _, container = client
    monkeypatch.setattr(container.settings.web, "public_url", "https://vpn.example.com")
    await bootstrap_public_urls(container)
    async with container.uow() as uow:
        cfg = container.bot_config
        # Trailing slash is required so the mini-app's relative assets resolve (see 1.2.8).
        assert (
            str(await cfg.value(uow, "SUBSCRIPTION_MINI_APP_URL")) == "https://vpn.example.com/app/"
        )
        assert str(await cfg.value(uow, "CABINET_URL")) == "https://vpn.example.com"
        # a value the owner set by hand is never overwritten on the next boot
        await cfg.set_values(uow, {"SUBSCRIPTION_MINI_APP_URL": "https://custom.example/mini"})
        await uow.commit()
    await bootstrap_public_urls(container)
    async with container.uow() as uow:
        keep = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL"))
        assert keep == "https://custom.example/mini"


# --- migration (bedolaga / 3x-ui) -------------------------------------------------------


def _bedolaga_db(path: Path) -> None:
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
    conn.execute(
        "INSERT INTO users VALUES (1, 555001, 'mig_alice', 'Alice', NULL, 'active', 'ru', "
        "12345, 1, 1, NULL, 'refMigAli1', NULL, "
        "'6f9619ff-8b86-4d01-b42d-00cf4fc964ff', '2025-01-15 12:00:00')"
    )
    conn.execute(
        "INSERT INTO subscriptions VALUES (1, 1, 'active', 0, '2025-01-15 12:00:00', "
        "'2099-01-01 00:00:00', 100, 1.5, 'https://sub.old/abc', NULL, 3, '[\"sq-1\"]', "
        "0, 3, NULL, 'shortuuid1', NULL, 'a1b2c3', 7, '2025-01-15 12:00:00')"
    )
    conn.execute(
        "INSERT INTO transactions VALUES (1, 1, 'deposit', 19900, NULL, 'pal24', "
        "'ext-mig-1', 1, '2025-05-01 10:00:00', '2025-05-01 10:01:00')"
    )
    conn.execute("INSERT INTO promocodes VALUES (1, 'MIG10', 'discount', 10, 24, 50, NULL, 1, 0)")
    conn.execute("INSERT INTO tariffs VALUES (7, 'Pro')")
    conn.commit()
    conn.close()


async def test_migration_bedolaga_upload_probe_run(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, container = client
    monkeypatch.chdir(tmp_path)  # migration_inbox/ is created relative to CWD
    auth = await _login(http)

    src = tmp_path / "bot.db"
    _bedolaga_db(src)
    res = await http.post(
        "/api/admin/migration/upload",
        headers=auth,
        files={"file": ("bot.db", src.read_bytes(), "application/octet-stream")},
    )
    assert res.status_code == 200, res.text
    file_id = res.json()["file_id"]

    res = await http.post(
        "/api/admin/migration/bedolaga/probe", headers=auth, json={"file_id": file_id}
    )
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert res.json()["counts"]["users"] == 1

    res = await http.post(
        "/api/admin/migration/bedolaga/run", headers=auth, json={"file_id": file_id}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["users_created"] == 1
    assert body["subscriptions"] == 1
    assert body["transactions"] == 1
    assert not (tmp_path / "migration_inbox" / f"{file_id}.src").exists()

    async with container.uow() as uow:
        user = await uow.users.find_one(telegram_id=555001)
        assert user is not None
        assert user.balance_minor == 12345  # kopeks adopted 1:1, not x100
        sub = await uow.subscriptions.find_one(user_id=user.id)
        assert sub is not None and str(sub.remnawave_uuid) == "6f9619ff-8b86-4d01-b42d-00cf4fc964ff"
        txn = await uow.transactions.find_one(external_id="ext-mig-1")
        assert txn is not None and txn.gateway_type is not None
        assert txn.gateway_type.value == "paypalych"  # pal24 -> paypalych


def _xui_db(path: Path) -> None:
    clients = {
        "clients": [
            {
                "id": "2d5a1f39-3c8e-4d5e-9a1b-0c2d3e4f5a6b",
                "email": "mike",
                "flow": "xtls-rprx-vision",
                "totalGB": 10737418240,
                "expiryTime": 4102444800000,
                "enable": True,
                "tgId": 555777,
                "subId": "mikesub123456789",
            }
        ]
    }
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE inbounds (
            id INTEGER PRIMARY KEY, user_id INTEGER, up INTEGER, down INTEGER, total INTEGER,
            remark TEXT, enable BOOLEAN, expiry_time INTEGER, listen TEXT, port INTEGER,
            protocol TEXT, settings TEXT, stream_settings TEXT, tag TEXT, sniffing TEXT
        );
        CREATE TABLE client_traffics (
            id INTEGER PRIMARY KEY, inbound_id INTEGER, enable BOOLEAN, email TEXT,
            up INTEGER, down INTEGER, expiry_time INTEGER, total INTEGER, reset INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO inbounds VALUES (1, 1, 0, 0, 0, 'DE-Reality', 1, 0, '', 443, 'vless', "
        "?, '{}', 'inbound-1', '{}')",
        (json.dumps(clients),),
    )
    conn.execute(
        "INSERT INTO client_traffics VALUES (1, 1, 1, 'mike', 1000, 2000, 4102444800000, "
        "10737418240, 0)"
    )
    conn.commit()
    conn.close()


async def test_migration_threexui_creates_panel_users(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, container = client
    monkeypatch.chdir(tmp_path)
    auth = await _login(http)

    src = tmp_path / "x-ui.db"
    _xui_db(src)
    res = await http.post(
        "/api/admin/migration/upload",
        headers=auth,
        files={"file": ("x-ui.db", src.read_bytes(), "application/octet-stream")},
    )
    assert res.status_code == 200, res.text
    file_id = res.json()["file_id"]

    res = await http.post(
        "/api/admin/migration/threexui/probe", headers=auth, json={"file_id": file_id}
    )
    assert res.status_code == 200, res.text
    probe_body = res.json()
    assert probe_body["ok"] is True
    assert probe_body["counts"]["with_telegram"] == 1
    assert probe_body["squads"], "probe must list panel squads"
    squad_uuid = probe_body["squads"][0]["uuid"]

    # No squad -> panel users would end up with zero inbounds; the server refuses.
    res = await http.post(
        "/api/admin/migration/threexui/run", headers=auth, json={"file_id": file_id}
    )
    assert res.status_code == 400

    res = await http.post(
        "/api/admin/migration/threexui/run",
        headers=auth,
        json={"file_id": file_id, "squad_uuid": squad_uuid},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["panel_users_created"] == 1

    assert len(container.remnawave_client.users) == 1  # created on the panel
    async with container.uow() as uow:
        user = await uow.users.find_one(telegram_id=555777)
        assert user is not None
        sub = await uow.subscriptions.find_one(user_id=user.id)
        assert sub is not None
        assert sub.short_id == "mikesub123456789"[:16]
        assert sub.traffic_limit_bytes == 10737418240  # totalGB is bytes, adopted verbatim


# --- telemetry / global error handling --------------------------------------------------


async def test_unhandled_error_returns_500_with_error_id(
    client: tuple[httpx.AsyncClient, ApiTestContainer],
) -> None:
    http, _ = client
    app = http._transport.app  # type: ignore[attr-defined]

    @app.get("/api/__telemetry_boom")
    async def _boom() -> None:
        raise RuntimeError("kaboom secret=topsecret")

    # The public site is mounted at "/" as a catch-all; move this late route ahead of it.
    app.router.routes.insert(0, app.router.routes.pop())

    # Starlette re-raises after the 500 handler runs (for server-side logging); uvicorn
    # swallows it and the client still gets the response. Emulate that, don't re-raise.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        res = await raw.get("/api/__telemetry_boom")
        assert res.status_code == 500
        body = res.json()
        assert body["ok"] is False
        assert body["error_id"].startswith("E")
        # The stack / exception text must never reach the client.
        assert "kaboom" not in res.text and "topsecret" not in res.text

        # A real 4xx (HTTPException) is NOT swallowed into a 500 by the catch-all handler.
        assert (await raw.get("/api/admin/dashboard")).status_code == 401
        assert (await raw.get("/api/nonexistent-route-xyz")).status_code == 404
