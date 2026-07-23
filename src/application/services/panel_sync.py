"""PanelSyncService — mirror Remnawave nodes/squads into local tables.

Called by the cabinet's «Синхронизировать» button and by the periodic scheduler job.
Upserts by panel uuid. A node that vanished from the panel is DELETED — but only on a
healthy sync (the panel returned a non-empty list), so an API hiccup returning nothing
never wipes every node. This removes nodes the owner deleted on the panel, and the stale
duplicate you'd otherwise see when a node is re-created with a new uuid.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from src.application.common.panel import RemnawaveClient
from src.core.enums import ServerNodeStatus
from src.infrastructure.database.models.server_node import ServerNode
from src.infrastructure.database.models.server_squad import ServerSquad

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork


class PanelSyncService:
    def __init__(self, client: RemnawaveClient) -> None:
        self._client = client

    async def sync_nodes(self, uow: UnitOfWork) -> int:
        """Pull panel nodes into ``server_nodes``; returns the number of live nodes."""
        panel_nodes = await self._client.get_nodes()
        now = dt.datetime.now(dt.UTC)

        existing = {n.node_uuid: n for n in await uow.server_nodes.list()}
        seen: set[object] = set()
        for pn in panel_nodes:
            seen.add(pn.uuid)
            row = existing.get(pn.uuid)
            if row is None:
                row = ServerNode(node_uuid=pn.uuid, name=pn.name)
                await uow.server_nodes.add(row)
            row.name = pn.name
            row.country_code = pn.country_code
            row.address = pn.address
            row.users_online = pn.users_online
            row.traffic_day_bytes = pn.traffic_used_bytes
            if pn.is_disabled:
                row.status = ServerNodeStatus.MAINTENANCE
            elif pn.is_online:
                row.status = ServerNodeStatus.ONLINE
            else:
                row.status = ServerNodeStatus.OFFLINE
            row.last_sync_at = now

        # Vanished from the panel -> DELETE, so a node the owner removed on the panel (or a
        # re-created one lingering under its old uuid as a stale duplicate) disappears here too.
        # Guard: only prune when the panel returned a healthy list — a hiccup that returns 0
        # nodes must not wipe every local node.
        if panel_nodes:
            for uuid_, row in existing.items():
                if uuid_ not in seen:
                    await uow.server_nodes.delete(row)

        await self.sync_squads(uow)
        return len(panel_nodes)

    async def sync_squads(self, uow: UnitOfWork) -> int:
        """Mirror internal squads (upsert by uuid; keep local pricing/flags)."""
        panel_squads = await self._client.get_internal_squads()
        existing = {sq.squad_uuid: sq for sq in await uow.server_squads.list()}
        for ps in panel_squads:
            row = existing.get(ps.uuid)
            if row is None:
                row = ServerSquad(squad_uuid=ps.uuid, display_name=ps.name, original_name=ps.name)
                await uow.server_squads.add(row)
            else:
                row.original_name = ps.name
                if not row.display_name:
                    row.display_name = ps.name
            row.current_users = ps.members_count
        return len(panel_squads)
