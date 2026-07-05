"""RemnawaveHttpClient request-building + response-parsing, mocked with respx.

Proves the auth headers (both X-Api-Key and Bearer), the {"response": ...} unwrapping, DTO
mapping and transient-retry behaviour without a live panel.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import respx

from src.application.dto.panel import ProvisionSpec
from src.core.config.remnawave import PanelAuthType, RemnawaveSettings
from src.infrastructure.remnawave.client import RemnawaveHttpClient
from src.infrastructure.remnawave.connection import build_profile

BASE = "https://panel.example.com"


def _client() -> RemnawaveHttpClient:
    cfg = RemnawaveSettings(base_url=BASE, auth_type=PanelAuthType.API_KEY, token="secret")
    return RemnawaveHttpClient.from_profile(build_profile(cfg))


@respx.mock
async def test_get_version_parses_and_unwraps() -> None:
    respx.get(f"{BASE}/api/system/health").mock(
        return_value=httpx.Response(200, json={"response": {"version": "2.8.3"}})
    )
    client = _client()
    try:
        version = await client.get_version()
    finally:
        await client.aclose()
    assert version.tuple == (2, 8, 3)


@respx.mock
async def test_create_user_sends_both_auth_headers_and_maps_dto() -> None:
    panel_uuid = uuid.uuid4()
    route = respx.post(f"{BASE}/api/users").mock(
        return_value=httpx.Response(
            201,
            json={
                "response": {
                    "uuid": str(panel_uuid),
                    "username": "sub_abc",
                    "status": "ACTIVE",
                    "trafficLimitBytes": 0,
                    "subscriptionUrl": "https://panel.example.com/sub/abc",
                }
            },
        )
    )
    client = _client()
    spec = ProvisionSpec(
        short_id="abc",
        telegram_id=42,
        username="sub_abc",
        expire_at=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        traffic_limit_bytes=0,
        device_limit=1,
    )
    try:
        user = await client.create_user(spec)
    finally:
        await client.aclose()

    assert user.uuid == panel_uuid
    assert user.subscription_url == "https://panel.example.com/sub/abc"
    request = route.calls.last.request
    assert request.headers["X-Api-Key"] == "secret"
    assert request.headers["Authorization"] == "Bearer secret"


@respx.mock
async def test_transient_5xx_is_retried_then_succeeds() -> None:
    route = respx.get(f"{BASE}/api/system/health").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"response": {"version": "2.8.0"}}),
        ]
    )
    client = _client()
    try:
        version = await client.get_version()
    finally:
        await client.aclose()
    assert version.tuple == (2, 8, 0)
    assert route.call_count == 2
