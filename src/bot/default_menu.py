"""Canonical bot menu — the single source of truth for menu actions.

Two things live here so they never drift:

- ``MENU_ACTIONS`` — every ``act:<code>`` the bot handles, with display labels and
  whether the action needs an active subscription. The cabinet constructor reads this
  (``GET /api/admin/bot-menu/actions``) to offer a dropdown instead of hardcoding codes.
- ``DEFAULT_MENU`` — the core buttons a fresh shop starts with. It backs BOTH the
  built-in fallback (``menu_render`` when the owner hasn't built a menu) and the
  "load default" action in the constructor (``POST /api/admin/bot-menu/reset-default``),
  so the seeded, editable menu matches what the bot shows out of the box.

Adding a new bot screen ⇒ add its handler (``bot/handlers``) and one row here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MenuAction:
    code: str  # the ``act:<code>`` payload
    label_ru: str
    label_en: str
    needs_subscription: bool = False


# Every action a menu button may point at. Keep in sync with the ``act:*`` handlers in
# ``src/bot/handlers/`` — the duplicate-action guard in the constructor relies on this.
MENU_ACTIONS: tuple[MenuAction, ...] = (
    MenuAction("buy", "Купить VPN", "Buy VPN"),
    MenuAction("subscription", "Моя подписка", "My subscription", needs_subscription=True),
    MenuAction("connect", "Подключить", "Connect", needs_subscription=True),
    MenuAction("devices", "Мои устройства", "My devices", needs_subscription=True),
    MenuAction("balance", "Баланс", "Balance"),
    MenuAction("history", "История платежей", "Payment history"),
    MenuAction("promocode", "Промокод", "Promo code"),
    MenuAction("referral", "Пригласить друга", "Refer a friend"),
    MenuAction("trial", "Пробный период", "Free trial"),
    MenuAction("cabinet", "Личный кабинет", "Account"),
    MenuAction("nodes", "Статус серверов", "Server status"),
    MenuAction("proxy", "MTProto-прокси", "MTProto proxy"),
    MenuAction("support", "Поддержка", "Support"),
)

_ACTIONS_BY_CODE = {a.code: a for a in MENU_ACTIONS}


def is_action(code: str) -> bool:
    return code in _ACTIONS_BY_CODE


def action(code: str) -> MenuAction | None:
    return _ACTIONS_BY_CODE.get(code)


@dataclass(frozen=True, slots=True)
class DefaultButton:
    label: str  # user-facing caption (emoji included)
    action: str  # a MENU_ACTIONS code
    color: str | None = None  # #RRGGBB hint → nearest Bot API button style


# The starter menu every shop begins with; the owner edits/reorders it in the constructor.
# Conditional buttons (trial, proxy, node status, admin) are added by the renderer at
# runtime rather than seeded, so they appear only when actually applicable.
DEFAULT_MENU: tuple[DefaultButton, ...] = (
    DefaultButton("🛒 Купить VPN", "buy", "#2ecc71"),
    DefaultButton("👤 Моя подписка", "subscription"),
    DefaultButton("🔌 Подключить", "connect", "#3498db"),
    DefaultButton("💰 Баланс", "balance"),
    DefaultButton("📊 История", "history"),
    DefaultButton("🎟 Промокод", "promocode"),
    DefaultButton("🎁 Пригласить друга", "referral"),
    DefaultButton("🆘 Поддержка", "support"),
)
