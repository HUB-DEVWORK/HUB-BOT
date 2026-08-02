"""Admin: Remnawave nodes mirror + sync (screen 12)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/servers")


def _row(n: Any) -> dict[str, Any]:
    return {
        "id": n.id,
        "uuid": str(n.node_uuid),
        "name": n.name,
        "country_code": n.country_code,
        "address": n.address,
        "status": n.status.value,
        "users_online": n.users_online,
        "traffic_day_bytes": n.traffic_day_bytes,
        "load_pct": n.load_pct,
        "ping_ms": n.ping_ms,
        "uptime_pct": n.uptime_pct,
        "is_for_sale": n.is_for_sale,
        "last_sync_at": iso(n.last_sync_at),
    }


@router.get("")
async def list_nodes(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        nodes = await uow.server_nodes.list()
        squads = await uow.server_squads.list()
    return {
        "panel_url": container.settings.remnawave.base_url,
        "items": [_row(n) for n in nodes],
        "squads": [
            {
                "id": sq.id,
                "name": sq.display_name,
                "original_name": sq.original_name,
                "uuid": str(sq.squad_uuid),
            }
            for sq in squads
        ],
    }


@router.post("/sync")
async def sync_nodes(
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Pull nodes from the panel into the local mirror. Returns fresh rows."""
    async with container.uow() as uow:
        try:
            synced = await container.panel_sync.sync_nodes(uow)
        except Exception as exc:
            raise HTTPException(502, f"panel sync failed: {exc}") from exc
        await audit(uow, identity, "servers.sync", None, nodes=synced)
        await uow.commit()
        nodes = await uow.server_nodes.list()
    return {"ok": True, "synced": synced, "items": [_row(n) for n in nodes]}


class SquadPatch(BaseModel):
    display_name: str  # "" -> reset to the panel name (follows panel renames again)


@router.patch("/squads/{squad_id}")
async def patch_squad(
    squad_id: int,
    body: SquadPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Buyer-facing squad name. An empty string resets back to the panel name."""
    async with container.uow() as uow:
        squad = await uow.server_squads.get(squad_id)
        if squad is None:
            raise HTTPException(404, "squad not found")
        name = body.display_name.strip()[:128]
        squad.display_name = name or (squad.original_name or "")
        await audit(uow, identity, "servers.squad_rename", f"squad:{squad.squad_uuid}", name=name)
        await uow.commit()
        result = {
            "id": squad.id,
            "name": squad.display_name,
            "original_name": squad.original_name,
            "uuid": str(squad.squad_uuid),
        }
    return {"ok": True, "squad": result}


class NodePatch(BaseModel):
    is_for_sale: bool


@router.patch("/{node_id}")
async def patch_node(
    node_id: int,
    body: NodePatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        node = await uow.server_nodes.get(node_id)
        if node is None:
            raise HTTPException(404, "node not found")
        node.is_for_sale = body.is_for_sale
        await audit(uow, identity, "servers.for_sale", f"node:{node.name}", on=body.is_for_sale)
        await uow.commit()
    return OkOut()
