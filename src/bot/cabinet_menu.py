"""Owner-configurable buttons of the «Личный кабинет» screen.

The cabinet screen (``act:cabinet``) renders a dynamic profile header plus a set of
account buttons. Which of them show, and in what order, is owner-controlled via the
``CABINET_BUTTONS`` config (a comma-separated list of keys, exactly like
``CONNECTION_APPS`` for the Connect tab) — so an owner can drop «История» or reorder
them without touching code. Buttons whose feature is switched off (balance, referral)
are skipped even when listed, so a disabled feature never shows a dead button.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CabinetButton:
    key: str  # stable id used in the CABINET_BUTTONS config
    label: str  # caption shown in the bot (emoji included)
    callback: str  # act:* callback data
    gate: str | None = None  # bot-config flag that must be truthy to show this button


# Every button the cabinet screen can offer, in the default order. The owner reorders /
# removes them through CABINET_BUTTONS; this tuple is the catalogue + the fallback order.
CABINET_BUTTONS: tuple[CabinetButton, ...] = (
    CabinetButton("subscription", "🔑 Моя подписка", "act:subscription:0"),
    CabinetButton("balance", "💰 Баланс", "act:balance:0", gate="BALANCE_ENABLED"),
    CabinetButton("history", "🧾 История", "act:history:0"),
    CabinetButton("referral", "🎁 Рефералка", "act:referral:0", gate="REFERRAL_ENABLED"),
    CabinetButton("promocode", "🎟 Промокод", "act:promocode"),
    CabinetButton("support", "🆘 Поддержка", "act:support:0"),
)

_BY_KEY = {b.key: b for b in CABINET_BUTTONS}
DEFAULT_ORDER = ",".join(b.key for b in CABINET_BUTTONS)


def parse_cabinet_buttons(raw: str | None) -> list[str]:
    """CABINET_BUTTONS ('subscription,balance,...') -> ordered list of known keys.

    Unknown/duplicate entries are dropped; an empty result falls back to the full
    default set so the cabinet is never left with no account buttons at all.
    """
    seen: set[str] = set()
    keys: list[str] = []
    for token in (raw or "").split(","):
        k = token.strip().lower()
        if k in _BY_KEY and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys or [b.key for b in CABINET_BUTTONS]


def cabinet_buttons(raw: str | None, *, flags: dict[str, bool]) -> list[tuple[str, str]]:
    """(label, callback) pairs for the enabled cabinet buttons in owner order, skipping any
    whose feature flag is off (``flags`` maps a gate name -> its current on/off state)."""
    out: list[tuple[str, str]] = []
    for key in parse_cabinet_buttons(raw):
        b = _BY_KEY[key]
        if b.gate is not None and not flags.get(b.gate, True):
            continue
        out.append((b.label, b.callback))
    return out
