"""PanelSyncService: a node that vanished from the panel is pruned (removes the stale
duplicate you'd otherwise see when a node is re-created), but a panel hiccup returning
nothing must not wipe every node."""

from __future__ import annotations

import uuid

import pytest

from src.application.dto.panel import PanelNode
from src.application.services.panel_sync import PanelSyncService
from src.infrastructure.database.models.server_node import ServerNode
from src.infrastructure.database.uow import UnitOfWork


class _FakeClient:
    def __init__(self, nodes: list[PanelNode]) -> None:
        self._nodes = nodes

    async def get_nodes(self) -> list[PanelNode]:
        return self._nodes

    async def get_internal_squads(self) -> list:
        return []


_KEEP = uuid.uuid4()
_GONE = uuid.uuid4()


async def _seed(uow: UnitOfWork) -> None:
    async with uow:
        await uow.server_nodes.add(ServerNode(node_uuid=_KEEP, name="KEEP", is_for_sale=True))
        await uow.server_nodes.add(ServerNode(node_uuid=_GONE, name="GONE-old", is_for_sale=True))
        await uow.commit()


@pytest.mark.asyncio
async def test_vanished_node_is_deleted(uow: UnitOfWork) -> None:
    await _seed(uow)
    client = _FakeClient([PanelNode(uuid=_KEEP, name="KEEP", is_online=True)])
    async with uow:
        await PanelSyncService(client).sync_nodes(uow)  # type: ignore[arg-type]
        await uow.commit()
    async with uow:
        left = {n.node_uuid for n in await uow.server_nodes.list()}
    assert left == {_KEEP}  # the vanished node (and its stale duplicate) is gone


@pytest.mark.asyncio
async def test_empty_panel_response_does_not_wipe_nodes(uow: UnitOfWork) -> None:
    await _seed(uow)
    client = _FakeClient([])  # panel hiccup / API error
    async with uow:
        await PanelSyncService(client).sync_nodes(uow)  # type: ignore[arg-type]
        await uow.commit()
    async with uow:
        left = {n.node_uuid for n in await uow.server_nodes.list()}
    assert left == {_KEEP, _GONE}  # nothing pruned on an empty response
