"""Bot routers, assembled in registration order (start -> menu -> flows)."""

from __future__ import annotations

from aiogram import Router

from src.bot.handlers import (
    actions,
    admin,
    promo,
    purchase,
    reply_menu,
    start,
    tickets,
    withdraw,
)


def build_router() -> Router:
    from src.bot.handlers.admin._common import ClearStaleForm

    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(admin.router)  # admin commands (/setlogo, …)
    root.include_router(promo.router)  # before tickets: state-gated code input wins
    root.include_router(withdraw.router)  # ditto: withdrawal details input
    root.include_router(purchase.router)
    root.include_router(reply_menu.router)  # before tickets: bottom-bar taps beat the catch-all
    root.include_router(tickets.router)
    root.include_router(actions.router)  # last: nav + generic actions
    # Tapping a menu/buy button means the user left any pending input flow — drop the FSM form so
    # a later stray text can't be booked as withdrawal details / a promo code. These routers' own
    # callbacks never depend on carried FSM state (act_cabinet clears anyway; purchase is
    # callback-data-driven), so an unconditional pre-clear is safe. The form routers (promo/
    # withdraw) re-arm inside their own handlers and tickets keeps its state, so they're excluded.
    actions.router.callback_query.middleware(ClearStaleForm())
    purchase.router.callback_query.middleware(ClearStaleForm())
    return root
