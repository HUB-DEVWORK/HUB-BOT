"""Webhook subscription resolution for Remnawave 3.0 payloads (no uuid, numeric id).

2.x events resolve by uuid as before. 3.0 events must find the sub via the numeric id
(once known), the ``sub_<short_id>`` username template, or a short_id adopted from the
panel shortUuid on import — and the first 3.0 event backfills ``remnawave_id``.
"""

from __future__ import annotations

import uuid as uuid_mod

from src.core.enums import SubscriptionStatus
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.remnawave.client import _to_panel_user
from src.web.routes.panel import resolve_subscription
from tests.factories import make_user


async def _make_sub(uow: UnitOfWork, **overrides: object) -> Subscription:
    async with uow:
        user = await make_user(uow, telegram_id=505)
        fields: dict[str, object] = {
            "user_id": user.id,
            "short_id": "abcdef1234",
            "status": SubscriptionStatus.ACTIVE,
        }
        fields.update(overrides)
        sub = Subscription(**fields)  # type: ignore[arg-type]
        await uow.subscriptions.add(sub)
        await uow.commit()
        return sub


def _v3_payload(**over: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 321,
        "shortUuid": "panelshort123456",
        "username": "sub_abcdef1234",
        "status": "ACTIVE",
        "userTraffic": {"usedTrafficBytes": 7},
    }
    payload.update(over)
    return payload


async def test_v2_payload_still_resolves_by_uuid(uow: UnitOfWork) -> None:
    panel_uuid = uuid_mod.uuid4()
    sub = await _make_sub(uow, remnawave_uuid=panel_uuid)
    panel_user = _to_panel_user({"uuid": str(panel_uuid), "username": "whatever"})
    async with uow:
        found = await resolve_subscription(uow, panel_user)
        assert found is not None and found.id == sub.id


async def test_v3_payload_resolves_by_backfilled_numeric_id(uow: UnitOfWork) -> None:
    sub = await _make_sub(uow, remnawave_id=321, short_id="zzzzzzzzzz")
    panel_user = _to_panel_user(_v3_payload(username="renamed_by_admin"))
    async with uow:
        found = await resolve_subscription(uow, panel_user)
        assert found is not None and found.id == sub.id


async def test_v3_payload_resolves_by_username_template_before_backfill(
    uow: UnitOfWork,
) -> None:
    # A sub provisioned on 2.x: only uuid + our short_id; the panel upgraded to 3.0.
    sub = await _make_sub(uow, remnawave_uuid=uuid_mod.uuid4())
    panel_user = _to_panel_user(_v3_payload())
    async with uow:
        found = await resolve_subscription(uow, panel_user)
        assert found is not None and found.id == sub.id


async def test_v3_payload_resolves_imported_sub_by_short_uuid(uow: UnitOfWork) -> None:
    # Imported subs stored the PANEL shortUuid as their short_id; username is foreign.
    sub = await _make_sub(uow, short_id="panelshort12345")
    panel_user = _to_panel_user(
        _v3_payload(username="legacy_client_77", shortUuid="panelshort12345")
    )
    async with uow:
        found = await resolve_subscription(uow, panel_user)
        assert found is not None and found.id == sub.id


async def test_v2_payload_never_falls_back_to_username(uow: UnitOfWork) -> None:
    # A uuid-carrying payload that matches nothing must NOT bind via username/short_id —
    # fallbacks are for uuid-less (3.0) events only.
    await _make_sub(uow, remnawave_uuid=uuid_mod.uuid4())
    foreign = _to_panel_user(
        {"uuid": str(uuid_mod.uuid4()), "username": "sub_abcdef1234", "shortUuid": "abcdef1234"}
    )
    async with uow:
        assert await resolve_subscription(uow, foreign) is None
