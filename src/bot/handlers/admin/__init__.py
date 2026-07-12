"""In-bot admin panel (owner/admin only).

Full management from the chat itself: stats, analytics, user management, broadcasts,
promocodes, gift-code batches, sale campaigns, quick toggles and branding. The whole
sub-tree is gated by a router-level ``IsAdmin`` filter, so non-admins fall through to
normal user handlers instead of relying on a per-handler guard we could forget.

Every screen reuses the same services/DAOs as the web cabinet — no logic is duplicated.
"""

from __future__ import annotations

from aiogram import Router

from src.bot.handlers.admin import (
    branding,
    broadcast,
    home,
    promos,
    settings,
    stats,
    users,
)
from src.bot.handlers.admin._common import ClearStaleForm, IsAdmin

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
# Inner (post-IsAdmin): any admin button press abandons a pending form, so a later
# stray number can't be booked as a balance change to the previously-targeted user.
router.callback_query.middleware(ClearStaleForm())

router.include_router(home.router)
router.include_router(stats.router)
router.include_router(users.router)
router.include_router(broadcast.router)
router.include_router(promos.router)
router.include_router(settings.router)
router.include_router(branding.router)

__all__ = ["router"]
