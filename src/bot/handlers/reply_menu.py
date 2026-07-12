"""Reply-keyboard (bottom-bar) dispatch — ``MAIN_MENU_MODE=reply``.

In reply mode the main menu is a persistent reply keyboard under the input. Its text buttons
send their label as a plain message; this router catches that label, maps it back to the menu
node and opens the matching screen — reusing the very same action handlers as the inline menu
(they accept a Message as well as a CallbackQuery). The mini-app button is a native web_app
button and opens the app directly, so it never reaches here.

Registered BEFORE tickets so a bottom-bar tap is never swallowed by the ticket catch-all. The
filter matches only a *current* top-level button label, in reply mode, outside any FSM state —
so ordinary free text and mid-flow input still fall through to their handlers.
"""

from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.keyboards import menu_keyboard
from src.bot.screen import show_screen
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

router = Router(name="reply_menu")

MenuMatch = tuple[str, str, int | None]  # (kind, payload/action, node_id)


async def _match_button(container: AppContainer, text: str) -> MenuMatch | None:
    """Resolve ``text`` to what the rendered bottom-bar would map it to, else None.

    Must mirror ``keyboards.reply_menu_markup`` exactly, or a visible button dead-ends into the
    ticket catch-all: (1) smart shortcuts the renderer appends; (2) the tree's renderable text
    buttons (action/screen/link — a mini-app is a web_app button that sends no text, back is
    skipped); (3) when the tree has none of those, the bar shows DEFAULT_MENU, so match that.
    """
    from src.bot.default_menu import DEFAULT_MENU, SMART_EXTRAS

    for label, code in SMART_EXTRAS:
        if text == label:
            return ("action", code, None)
    async with container.uow() as uow:
        nodes = list(await uow.menu_nodes.tree())
    text_nodes = [
        n
        for n in nodes
        if n.parent_id is None and n.is_active and n.kind.value in ("action", "screen", "link")
    ]
    if text_nodes:
        node = next((n for n in text_nodes if n.label == text), None)
        return (node.kind.value, node.payload or "", node.id) if node else None
    button = next((b for b in DEFAULT_MENU if b.label == text), None)
    return ("action", button.action, None) if button else None


class ReplyMenuButton(BaseFilter):
    """Passes only for a current bottom-bar button label (reply mode, outside any FSM state)."""

    async def __call__(self, message: Message, **data: Any) -> bool | dict[str, Any]:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        container = data.get("container")
        if not isinstance(container, AppContainer):
            return False
        state = data.get("state")
        if isinstance(state, FSMContext) and await state.get_state() is not None:
            return False  # mid-flow (promocode/ticket/withdraw input) — don't hijack
        async with container.uow() as uow:
            mode = str(await container.bot_config.value(uow, "MAIN_MENU_MODE") or "inline")
        if mode != "reply":
            return False
        match = await _match_button(container, text)
        return {"menu_match": match} if match else False


async def maybe_dispatch_menu_button(
    message: Message, container: AppContainer, db_user: User, state: FSMContext
) -> bool:
    """When a text handler is waiting on FSM input, let a bottom-bar tap escape it.

    promo/withdraw register their state-gated text handlers BEFORE this router, so while
    ``PromoForm.waiting_code`` / ``WithdrawForm.waiting_details`` is set a persistent-menu
    tap reaches them and would be eaten as the code/details. Those handlers call this first:
    if ``message.text`` exactly matches a current bottom-bar label (reply mode only, derived
    the same way as :class:`ReplyMenuButton`), clear the state and open that menu action,
    returning True so the caller stops. Real codes/details never match an emoji menu label.
    """
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return False
    async with container.uow() as uow:
        mode = str(await container.bot_config.value(uow, "MAIN_MENU_MODE") or "inline")
    if mode != "reply":
        return False
    match = await _match_button(container, text)
    if match is None:
        return False
    await state.clear()  # abort the pending form — the user chose a menu button instead
    await dispatch(message, container, db_user, state, match)
    return True


@router.message(ReplyMenuButton())
async def dispatch(
    message: Message,
    container: AppContainer,
    db_user: User,
    state: FSMContext,
    menu_match: MenuMatch,
) -> None:
    kind, payload, node_id = menu_match
    if kind == "action":
        await _open_action(message, container, db_user, state, payload)
    elif kind == "screen" and node_id is not None:
        await _open_screen(message, container, node_id)
    elif kind == "link" and payload:
        # Reply keyboards can't carry url buttons, so a link node just sends its address.
        await message.answer(payload)


async def _open_action(
    message: Message, container: AppContainer, db_user: User, state: FSMContext, code: str
) -> None:
    """Open a built-in action by its MENU_ACTIONS code, reusing the inline handlers."""
    from src.bot.handlers import actions, promo, purchase

    if code == "promocode":
        await promo.ask_code(message, container, db_user, state)
        return
    handlers = {
        "buy": purchase.open_buy,
        "cabinet": actions.act_cabinet,
        "subscription": actions.act_subscription,
        "connect": actions.act_connect,
        "devices": actions.act_devices,
        "balance": actions.act_balance,
        "history": actions.act_history,
        "referral": actions.act_referral,
        "trial": actions.act_trial,
        "nodes": actions.act_nodes,
        "proxy": actions.act_proxy,
        "support": actions.act_support,
    }
    handler = handlers.get(code)
    if handler is not None:
        await handler(message, container, db_user)


async def _open_screen(message: Message, container: AppContainer, node_id: int) -> None:
    """Open a constructor sub-screen (its child buttons) as a fresh inline message."""
    async with container.uow() as uow:
        nodes = list(await uow.menu_nodes.tree())
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
    node = next((n for n in nodes if n.id == node_id), None)
    if node is None:
        return
    text = node.payload or node.label
    markup = menu_keyboard(nodes, node.id, miniapp_url=miniapp_url or None, with_back=True)
    await show_screen(message, text, markup, parse_mode=None)
