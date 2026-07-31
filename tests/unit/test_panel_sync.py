"""PanelSyncService: a node that vanished from the panel is pruned (removes the stale
duplicate you'd otherwise see when a node is re-created), but a panel hiccup returning
nothing must not wipe every node."""

from __future__ import annotations

import uuid

import pytest

from src.application.dto.panel import PanelNode, PanelSquad
from src.application.services.panel_sync import PanelSyncService
from src.infrastructure.database.models.server_node import ServerNode
from src.infrastructure.database.models.server_squad import ServerSquad
from src.infrastructure.database.uow import UnitOfWork


class _FakeClient:
    def __init__(self, nodes: list[PanelNode], squads: list[PanelSquad] | None = None) -> None:
        self._nodes = nodes
        self._squads = squads or []

    async def get_nodes(self) -> list[PanelNode]:
        return self._nodes

    async def get_internal_squads(self) -> list[PanelSquad]:
        return self._squads


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


# --- squads: same prune contract as nodes ("сквады не обновляются" — deleted ones stayed) ---

_SQ_KEEP = uuid.uuid4()
_SQ_GONE = uuid.uuid4()


async def _seed_squads(uow: UnitOfWork) -> None:
    async with uow:
        await uow.server_squads.add(
            ServerSquad(squad_uuid=_SQ_KEEP, display_name="KEEP", original_name="KEEP")
        )
        await uow.server_squads.add(
            ServerSquad(squad_uuid=_SQ_GONE, display_name="GONE", original_name="GONE")
        )
        await uow.commit()


@pytest.mark.asyncio
async def test_vanished_squad_is_deleted(uow: UnitOfWork) -> None:
    """A squad deleted on the panel must disappear from the tariff editor, not linger forever."""
    await _seed_squads(uow)
    client = _FakeClient([], [PanelSquad(uuid=_SQ_KEEP, name="KEEP")])
    async with uow:
        await PanelSyncService(client).sync_squads(uow)  # type: ignore[arg-type]
        await uow.commit()
    async with uow:
        left = {s.squad_uuid for s in await uow.server_squads.list()}
    assert left == {_SQ_KEEP}


@pytest.mark.asyncio
async def test_empty_squad_response_does_not_wipe_squads(uow: UnitOfWork) -> None:
    """A panel hiccup returning 0 squads must not wipe them (that would break provisioning)."""
    await _seed_squads(uow)
    client = _FakeClient([], [])
    async with uow:
        await PanelSyncService(client).sync_squads(uow)  # type: ignore[arg-type]
        await uow.commit()
    async with uow:
        left = {s.squad_uuid for s in await uow.server_squads.list()}
    assert left == {_SQ_KEEP, _SQ_GONE}
