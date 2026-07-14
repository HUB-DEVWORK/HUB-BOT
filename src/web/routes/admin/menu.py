"""Admin: bot menu constructor (screen 05) — read/replace the button tree."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.bot.default_menu import DEFAULT_MENU, MENU_ACTIONS
from src.core.enums import MenuNodeKind
from src.infrastructure.database.models.menu_node import MenuNode
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/bot-menu")


class NodeIn(BaseModel):
    # Client-side ids are opaque strings; parent refs use the same ids.
    id: str = Field(min_length=1, max_length=36)
    parent: str | None = None
    label: str = Field(min_length=1, max_length=64)
    kind: MenuNodeKind = MenuNodeKind.ACTION
    payload: str | None = Field(None, max_length=4096)
    custom_emoji_id: str | None = Field(None, max_length=32)
    color: str | None = Field(None, max_length=9)
    image_path: str | None = Field(None, max_length=512)
    is_active: bool = True
    row_index: int = Field(0, ge=0)  # buttons sharing a row_index sit side by side
    # None (field omitted by an older SPA) -> fall back to array position; an explicit value
    # (incl. 0) from the editor is honoured so reordering persists.
    order_index: int | None = Field(None, ge=0)

    @field_validator("color")
    @classmethod
    def _hex_color(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not (v.startswith("#") and len(v) in (4, 7, 9)):
            raise ValueError("color must be #RGB/#RRGGBB/#RRGGBBAA")
        return v


class TreeIn(BaseModel):
    nodes: list[NodeIn]


def _serialize(nodes: list[MenuNode]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(n.id),
            "parent": str(n.parent_id) if n.parent_id is not None else None,
            "label": n.label,
            "kind": n.kind.value,
            "payload": n.payload,
            "custom_emoji_id": n.custom_emoji_id,
            "color": n.color,
            "image_path": n.image_path,
            "is_active": n.is_active,
            "order_index": n.order_index,
            "row_index": n.row_index,
        }
        for n in nodes
    ]


def _default_menu_rows() -> list[MenuNode]:
    """DEFAULT_MENU as fresh top-level ACTION nodes — shared by reset + first-boot seed."""
    return [
        MenuNode(
            parent_id=None,
            order_index=i,
            row_index=b.row,
            label=b.label,
            kind=MenuNodeKind.ACTION,
            payload=b.action,
            color=b.color,
        )
        for i, b in enumerate(DEFAULT_MENU)
    ]


# Top-level action sets of menus we shipped as defaults in earlier versions. A live menu
# whose top-level actions match one of these was our seed (not the owner's work), so a
# later deploy may upgrade it to the current DEFAULT_MENU. A customized menu — any other
# action set — is never touched.
_LEGACY_DEFAULT_SIGNATURES: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "cabinet",
            "buy",
            "subscription",
            "connect",
            "balance",
            "history",
            "promocode",
            "referral",
            "support",
        }
    ),
)


async def bootstrap_menu(container: AppContainer) -> None:
    """Seed the default menu on first boot; on later boots, upgrade an *unmodified* older
    default to the current one. Called from the app lifespan and safe to run on every start:
    the owner's own menu (a different action set) is left untouched.
    """
    async with container.uow() as uow:
        top = [n for n in await uow.menu_nodes.tree() if n.parent_id is None]
        current = frozenset(n.payload for n in top if n.kind is MenuNodeKind.ACTION and n.payload)
        target = frozenset(b.action for b in DEFAULT_MENU)
        # Non-empty menu that is already current OR was customized by the owner -> leave it.
        if top and (current == target or current not in _LEGACY_DEFAULT_SIGNATURES):
            return
        await uow.menu_nodes.delete_by()
        for row in _default_menu_rows():
            await uow.menu_nodes.add(row)
        await uow.commit()


@router.get("")
async def get_menu(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        nodes = list(await uow.menu_nodes.tree())
    return {"nodes": _serialize(nodes)}


@router.put("")
async def save_menu(
    body: TreeIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    # Validate parent references + no cycles (parents must appear earlier or exist).
    ids = {n.id for n in body.nodes}
    if len(ids) != len(body.nodes):
        raise HTTPException(400, "duplicate node ids")
    for n in body.nodes:
        if n.parent is not None and n.parent not in ids:
            raise HTTPException(400, f"node {n.id}: unknown parent {n.parent}")
        if n.parent == n.id:
            raise HTTPException(400, f"node {n.id}: self-parent")

    # Insert parents-first, mapping client ids -> DB ids.
    async with container.uow() as uow:
        await uow.menu_nodes.delete_by()
        id_map: dict[str, int] = {}
        pending = list(body.nodes)
        # Honour the editor's explicit order_index so reordering persists instead of snapping
        # back to creation order. move() assigns a unique 0..n-1 per parent (a swap), so
        # `n.order_index` is authoritative — no falsy-0 fallback (that mislaid a top-moved
        # button). array_pos is only a last resort when a client omits it entirely.
        array_pos: dict[str | None, int] = {}
        guard = 0
        while pending:
            guard += 1
            if guard > len(body.nodes) + 2:
                raise HTTPException(400, "menu tree contains a cycle")
            progressed = False
            rest: list[NodeIn] = []
            for n in pending:
                if n.parent is None or n.parent in id_map:
                    pos = array_pos.get(n.parent, 0)
                    array_pos[n.parent] = pos + 1
                    row = MenuNode(
                        parent_id=id_map.get(n.parent) if n.parent else None,
                        order_index=n.order_index if n.order_index is not None else pos,
                        row_index=n.row_index,
                        label=n.label,
                        kind=n.kind,
                        payload=n.payload,
                        custom_emoji_id=n.custom_emoji_id or None,
                        color=n.color,
                        image_path=n.image_path or None,
                        is_active=n.is_active,
                    )
                    await uow.menu_nodes.add(row)
                    id_map[n.id] = row.id
                    progressed = True
                else:
                    rest.append(n)
            if not progressed:
                raise HTTPException(400, "menu tree contains a cycle")
            pending = rest
        await audit(uow, identity, "menu.save", None, count=len(body.nodes))
        await uow.commit()
        nodes = list(await uow.menu_nodes.tree())
    return {"ok": True, "nodes": _serialize(nodes)}


@router.get("/actions")
async def list_actions() -> dict[str, Any]:
    """Catalogue of bot actions a button can point at — feeds the constructor's dropdown."""
    return {
        "actions": [
            {
                "code": a.code,
                "label_ru": a.label_ru,
                "label_en": a.label_en,
                "needs_subscription": a.needs_subscription,
            }
            for a in MENU_ACTIONS
        ]
    }


@router.post("/reset-default")
async def reset_default(
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Replace the menu with the built-in default — a real, editable starting menu."""
    async with container.uow() as uow:
        await uow.menu_nodes.delete_by()
        for row in _default_menu_rows():
            await uow.menu_nodes.add(row)
        await audit(uow, identity, "menu.reset_default", None, count=len(DEFAULT_MENU))
        await uow.commit()
        nodes = list(await uow.menu_nodes.tree())
    return {"ok": True, "nodes": _serialize(nodes)}
