"""Bot routers, assembled in registration order (start -> menu -> flows)."""

from __future__ import annotations

from aiogram import Router

from src.bot.handlers import actions, purchase, start, tickets


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(purchase.router)
    root.include_router(tickets.router)
    root.include_router(actions.router)  # last: nav + generic actions
    return root
