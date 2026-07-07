"""Admin + cabinet API integration tests: ASGI app over in-memory sqlite + fakes."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from collections.abc import AsyncIterator
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
    assert len(nodes) >= 6
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
