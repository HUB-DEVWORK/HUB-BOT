"""RemnawaveHttpClient against the 3.0 contract, mocked with respx.

Remnawave 3.0 keys users by a numeric id (no uuid), filters by telegramId via
/users/stream, renamed ip-control to connections and moved drop-connections to its
own route. These tests pin the dual-contract routing: the client probes the version
once and speaks 3.0 for user-scoped calls, including re-resolving the numeric id for
subscriptions that only know their 2.x uuid + short_id.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import respx

from src.application.dto.panel import PanelUserRef, ProvisionSpec
from src.core.config.remnawave import PanelAuthType, RemnawaveSettings
from src.infrastructure.remnawave.client import RemnawaveHttpClient
from src.infrastructure.remnawave.connection import build_profile

BASE = "https://panel.example.com"


def _client() -> RemnawaveHttpClient:
    cfg = RemnawaveSettings(base_url=BASE, auth_type=PanelAuthType.API_KEY, token="secret")
    return RemnawaveHttpClient.from_profile(build_profile(cfg))


def _mock_v3() -> None:
    respx.get(f"{BASE}/api/system/metadata").mock(
        return_value=httpx.Response(200, json={"response": {"version": "3.0.0"}})
    )


def _user_payload(panel_id: int = 101) -> dict[str, object]:
    return {
        "response": {
            "id": panel_id,
            "shortUuid": "shrt" * 4,
            "username": "sub_abc",
            "status": "ACTIVE",
            "trafficLimitBytes": 0,
            "userTraffic": {"usedTrafficBytes": "1024"},
            "activeInternalSquads": [{"uuid": str(uuid.uuid4()), "name": "eu"}],
            "subscriptionUrl": f"{BASE}/sub/abc",
        }
    }


@respx.mock
async def test_v3_get_user_routes_by_numeric_id_and_maps_dto() -> None:
    _mock_v3()
    respx.get(f"{BASE}/api/users/101").mock(
        return_value=httpx.Response(200, json=_user_payload(101))
    )
    client = _client()
    try:
        user = await client.get_user(PanelUserRef(panel_id=101))
    finally:
        await client.aclose()
    assert user is not None
    assert user.uuid is None
    assert user.panel_id == 101
    assert user.traffic_used_bytes == 1024
    # 3.0 ships squads as {uuid, name} objects — mapped back to uuid strings
    assert len(user.internal_squads) == 1
    assert "-" in user.internal_squads[0]


@respx.mock
async def test_v3_resolves_uuid_only_ref_via_username_then_caches() -> None:
    _mock_v3()
    resolve = respx.post(f"{BASE}/api/users/resolve").mock(
        return_value=httpx.Response(
            200, json={"response": {"id": 77, "username": "sub_abcdef1234", "shortUuid": "x" * 16}}
        )
    )
    action = respx.post(f"{BASE}/api/users/77/actions/enable").mock(
        return_value=httpx.Response(200, json={"response": {}})
    )
    ref = PanelUserRef(uuid=uuid.uuid4(), short_id="abcdef1234")  # 2.x-provisioned sub
    client = _client()
    try:
        await client.enable_user(ref)
        await client.enable_user(ref)  # second call must hit the id cache
    finally:
        await client.aclose()
    assert action.call_count == 2
    assert resolve.call_count == 1
    assert json.loads(resolve.calls.last.request.content) == {"username": "sub_abcdef1234"}


@respx.mock
async def test_v3_update_user_sends_numeric_id_and_clamps_past_expiry() -> None:
    _mock_v3()
    route = respx.patch(f"{BASE}/api/users").mock(
        return_value=httpx.Response(200, json=_user_payload(55))
    )
    spec = ProvisionSpec(
        short_id="abc",
        telegram_id=42,
        username="sub_abc",
        expire_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),  # deliberately in the past
        traffic_limit_bytes=0,
        device_limit=None,
    )
    client = _client()
    try:
        await client.update_user(PanelUserRef(panel_id=55), spec)
    finally:
        await client.aclose()
    body = json.loads(route.calls.last.request.content)
    assert body["id"] == 55
    assert "uuid" not in body
    # 3.0 rejects a past expireAt — the client clamps it to near-now instead
    assert dt.datetime.fromisoformat(body["expireAt"]) > dt.datetime.now(dt.UTC)


@respx.mock
async def test_v3_get_user_by_telegram_id_uses_stream_filter() -> None:
    _mock_v3()
    route = respx.get(f"{BASE}/api/users/stream").mock(
        return_value=httpx.Response(
            200, json={"response": {"users": [_user_payload(9)["response"]], "hasMore": False}}
        )
    )
    client = _client()
    try:
        user = await client.get_user_by_telegram_id(4242)
    finally:
        await client.aclose()
    assert user is not None and user.panel_id == 9
    assert route.calls.last.request.url.params["telegramId"] == "4242"


@respx.mock
async def test_v3_devices_and_drop_use_numeric_user_id() -> None:
    _mock_v3()
    respx.get(f"{BASE}/api/hwid/devices/101").mock(
        return_value=httpx.Response(
            200,
            json={"response": {"total": 1, "devices": [{"hwid": "h1", "platform": "ios"}]}},
        )
    )
    delete = respx.post(f"{BASE}/api/hwid/devices/delete").mock(
        return_value=httpx.Response(200, json={"response": {"total": 0, "devices": []}})
    )
    drop = respx.post(f"{BASE}/api/connections/drop").mock(
        return_value=httpx.Response(200, json={"response": {}})
    )
    ref = PanelUserRef(panel_id=101)
    client = _client()
    try:
        devices = await client.get_devices(ref)
        await client.delete_device(ref, "h1")
        await client.drop_connections(ref)
    finally:
        await client.aclose()
    assert [d.hwid for d in devices] == ["h1"]
    assert json.loads(delete.calls.last.request.content) == {"userId": 101, "hwid": "h1"}
    assert json.loads(drop.calls.last.request.content) == {
        "dropBy": {"by": "userIds", "userIds": [101]},
        "targetNodes": {"target": "allNodes"},
    }


@respx.mock
async def test_v3_drop_tolerates_no_connected_nodes() -> None:
    # Seen live on 3.0.0: /api/connections/drop 404s with "Connected nodes not found"
    # when the panel has zero connected nodes. Nothing to drop is success, not an error.
    _mock_v3()
    respx.post(f"{BASE}/api/connections/drop").mock(
        return_value=httpx.Response(
            404, json={"message": "Connected nodes not found", "statusCode": 404}
        )
    )
    client = _client()
    try:
        await client.drop_connections(PanelUserRef(panel_id=101))  # must not raise
    finally:
        await client.aclose()


@respx.mock
async def test_v3_connections_job_replaces_ip_control() -> None:
    _mock_v3()
    node = str(uuid.uuid4())
    respx.post(f"{BASE}/api/connections/by-node/{node}").mock(
        return_value=httpx.Response(200, json={"response": {"jobId": "job-7"}})
    )
    respx.get(f"{BASE}/api/connections/by-node/job-7").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "isCompleted": True,
                    "isFailed": False,
                    "result": {
                        "success": True,
                        "nodeUuid": node,
                        "users": [{"userId": 101, "ips": [{"ip": "1.2.3.4"}]}],
                    },
                }
            },
        )
    )
    client = _client()
    try:
        job_id = await client.start_users_ips_job(node)
        result = await client.get_users_ips_result(job_id)
    finally:
        await client.aclose()
    assert job_id == "job-7"
    assert result == [("101", ["1.2.3.4"])]


@respx.mock
async def test_v3_internal_squads_read_members_count_from_info() -> None:
    _mock_v3()
    respx.get(f"{BASE}/api/internal-squads").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "total": 1,
                    "internalSquads": [
                        {
                            "uuid": str(uuid.uuid4()),
                            "name": "eu-west",
                            "info": {"membersCount": 12, "inboundsCount": 2},
                        }
                    ],
                }
            },
        )
    )
    client = _client()
    try:
        squads = await client.get_internal_squads()
    finally:
        await client.aclose()
    assert squads[0].name == "eu-west"
    assert squads[0].members_count == 12
