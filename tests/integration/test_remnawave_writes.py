"""Write-path verification against a MOCK panel (respx) — no real server touched.

Confirms the concrete client issues the right method/path/payload for create/update/delete/
revoke/actions, using the panel-verified field names (externalSquadUuid, shortUuid), and that
SubscriptionService.renew pushes the new expiry to the panel.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import respx

from src.application.dto.panel import ProvisionSpec
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.config.remnawave import PanelAuthType, RemnawaveSettings
from src.core.enums import Currency, SubscriptionStatus
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.user import User
from src.infrastructure.remnawave.client import RemnawaveHttpClient
from src.infrastructure.remnawave.connection import build_profile
from tests.fakes import FakeRemnawaveClient

BASE = "https://panel.example.com"


def _client() -> RemnawaveHttpClient:
    cfg = RemnawaveSettings(base_url=BASE, auth_type=PanelAuthType.API_KEY, token="secret")
    return RemnawaveHttpClient.from_profile(build_profile(cfg))


def _spec(**over: object) -> ProvisionSpec:
    base: dict[str, object] = {
        "short_id": "s",
        "telegram_id": 42,
        "username": "sub_s",
        "expire_at": dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        "traffic_limit_bytes": 0,
        "device_limit": 2,
        "internal_squads": ("squad-1",),
        "external_squad": "ext-1",
    }
    base.update(over)
    return ProvisionSpec(**base)  # type: ignore[arg-type]


@respx.mock
async def test_create_user_payload_uses_panel_verified_field_names() -> None:
    panel_uuid = uuid.uuid4()
    route = respx.post(f"{BASE}/api/users").mock(
        return_value=httpx.Response(
            201,
            json={
                "response": {
                    "uuid": str(panel_uuid),
                    "shortUuid": "s",
                    "username": "sub_s",
                    "status": "ACTIVE",
                    "trafficLimitBytes": 0,
                    "subscriptionUrl": f"{BASE}/sub/s",
                    "activeInternalSquads": ["squad-1"],
                    "externalSquadUuid": "ext-1",
                }
            },
        )
    )
    client = _client()
    try:
        user = await client.create_user(_spec())
    finally:
        await client.aclose()

    body = json.loads(route.calls.last.request.content)
    assert body["username"] == "sub_s"
    assert body["activeInternalSquads"] == ["squad-1"]
    assert body["externalSquadUuid"] == "ext-1"  # verified name, NOT activeExternalSquad
    assert body["hwidDeviceLimit"] == 2
    assert body["telegramId"] == 42
    assert "expireAt" in body
    # response parsing uses the verified names
    assert user.short_id == "s"  # from shortUuid
    assert user.external_squad == "ext-1"
    assert user.internal_squads == ("squad-1",)
    # both auth headers were sent
    assert route.calls.last.request.headers["X-Api-Key"] == "secret"
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


@respx.mock
async def test_update_user_patches_users_collection_with_uuid_in_body() -> None:
    # Backend v2: PATCH /api/users (collection) with the uuid in the body —
    # NOT PATCH /api/users/{uuid}, which 404s on a live 2.x panel.
    panel_uuid = uuid.uuid4()
    route = respx.patch(f"{BASE}/api/users").mock(
        return_value=httpx.Response(
            200, json={"response": {"uuid": str(panel_uuid), "shortUuid": "s"}}
        )
    )
    client = _client()
    try:
        await client.update_user(panel_uuid, _spec())
    finally:
        await client.aclose()
    assert route.called
    assert route.calls.last.request.url.path == "/api/users"  # no uuid in the path
    body = json.loads(route.calls.last.request.content)
    assert body["uuid"] == str(panel_uuid)


@respx.mock
async def test_delete_user_issues_delete() -> None:
    panel_uuid = uuid.uuid4()
    route = respx.delete(f"{BASE}/api/users/{panel_uuid}").mock(return_value=httpx.Response(200))
    client = _client()
    try:
        await client.delete_user(panel_uuid)
    finally:
        await client.aclose()
    assert route.called


@respx.mock
async def test_revoke_and_user_actions_hit_action_endpoints() -> None:
    panel_uuid = uuid.uuid4()
    base = f"{BASE}/api/users/{panel_uuid}/actions"
    revoke = respx.post(f"{base}/revoke").mock(
        return_value=httpx.Response(
            200,
            json={"response": {"uuid": str(panel_uuid), "subscriptionUrl": f"{BASE}/sub/s?r=1"}},
        )
    )
    enable = respx.post(f"{base}/enable").mock(return_value=httpx.Response(200))
    disable = respx.post(f"{base}/disable").mock(return_value=httpx.Response(200))
    reset = respx.post(f"{base}/reset-traffic").mock(return_value=httpx.Response(200))
    drop = respx.post(f"{base}/drop-connections").mock(return_value=httpx.Response(200))

    client = _client()
    try:
        revoked = await client.revoke_subscription(panel_uuid)
        await client.enable_user(panel_uuid)
        await client.disable_user(panel_uuid)
        await client.reset_traffic(panel_uuid)
        await client.drop_connections(panel_uuid)
    finally:
        await client.aclose()

    assert revoke.called and enable.called and disable.called and reset.called and drop.called
    assert revoked.subscription_url is not None and revoked.subscription_url.endswith("?r=1")


async def test_renew_extends_expiry_and_updates_panel_on_mock() -> None:
    fake = FakeRemnawaveClient()
    service = SubscriptionService(RemnawaveService(fake))
    panel_uuid = uuid.uuid4()
    user = User(id=1, telegram_id=5, referral_code="x", currency=Currency.RUB)
    sub = Subscription(
        user_id=1,
        remnawave_uuid=panel_uuid,
        short_id="abc",
        status=SubscriptionStatus.ACTIVE,
        expire_at=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        traffic_limit_bytes=0,
        internal_squads=["squad-1"],
    )
    sub.user = user  # in-memory relationship, no session needed

    before = sub.expire_at
    await service.renew(uow=None, subscription=sub, days=30)  # type: ignore[arg-type]

    assert sub.expire_at is not None and before is not None and sub.expire_at > before
    assert sub.status is SubscriptionStatus.ACTIVE
    # the panel user was updated (mock recorded it under the same uuid)
    assert panel_uuid in fake.users


async def test_set_expiry_sets_absolute_date_may_shorten_and_updates_panel() -> None:
    """set_expiry moves the expiry to an absolute date (calendar) — can extend OR shorten."""
    fake = FakeRemnawaveClient()
    service = SubscriptionService(RemnawaveService(fake))
    panel_uuid = uuid.uuid4()
    user = User(id=1, telegram_id=5, referral_code="x", currency=Currency.RUB)
    sub = Subscription(
        user_id=1,
        remnawave_uuid=panel_uuid,
        short_id="abc",
        status=SubscriptionStatus.ACTIVE,
        expire_at=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        traffic_limit_bytes=0,
        internal_squads=["squad-1"],
    )
    sub.user = user

    # Shorten to an earlier (but still future) date — renew() could never do this.
    target = dt.datetime(2027, 6, 15, 23, 59, 59, tzinfo=dt.UTC)
    await service.set_expiry(uow=None, subscription=sub, expire_at=target)  # type: ignore[arg-type]
    assert sub.expire_at == target
    assert sub.status is SubscriptionStatus.ACTIVE
    assert panel_uuid in fake.users  # pushed to the panel

    # A past date flips the subscription to EXPIRED.
    past = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
    await service.set_expiry(uow=None, subscription=sub, expire_at=past)  # type: ignore[arg-type]
    assert sub.expire_at == past
    assert sub.status is SubscriptionStatus.EXPIRED
