"""Owner-configurable buttons of the «Личный кабинет» screen.

Historically ``CABINET_BUTTONS`` held a comma-separated list of catalogue keys (order +
which buttons show). It now holds a JSON array so an owner can additionally RENAME buttons,
put a premium (custom) emoji on them, and ADD their own buttons that point at any bot action.
The legacy CSV form is still read and auto-upgraded, so existing installs keep working without
a migration.

JSON item shapes::

    {"key": "subscription", "label": "🔑 Моя подписка", "enabled": true}          # built-in
    {"key": "balance", "label": "💰 Баланс", "icon": "5368324170671202286"}        # + custom emoji
    {"key": "c1", "label": "🎉 Акции", "action": "promocode", "enabled": true}     # custom

Built-in items may override ``label``; their callback and feature-gate come from the catalogue.
Custom items carry their own label and an ``action`` code, rendered as ``act:<action>:0`` (the
same convention the main menu uses — see ``bot/menu_render``). Any item may carry an ``icon`` —
the numeric ``custom_emoji_id`` of a premium emoji; it is passed to Telegram as the button's
``icon_custom_emoji_id`` (see LesVPN MenuLayout for the same mechanism). A gated built-in whose
feature is switched off (balance/referral) is skipped even when listed, so a disabled feature
never shows a dead button.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CabinetButton:
    key: str  # stable id used in the CABINET_BUTTONS config
    label: str  # default caption shown in the bot (emoji included)
    callback: str  # act:* callback data
    gate: str | None = None  # bot-config flag that must be truthy to show this button


# Every built-in button the cabinet screen can offer, in the default order. The owner reorders /
# removes / renames them (and adds custom ones) through CABINET_BUTTONS; this tuple is the
# catalogue + the fallback order + the source of default labels, gates and callbacks.
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


def custom_callback(action: str | None) -> str:
    """Cabinet custom button -> bot callback. Mirrors the main menu: ``act:<code>:0``."""
    return f"act:{(action or '').strip().lower()}:0"


def _custom_target(it: dict, extra: dict) -> tuple[str, dict]:
    """Resolve a custom cabinet button to (callback_data, extra) by its ``btype``.

    ``action`` -> ``act:<code>:0`` callback (default). ``link``/``miniapp`` carry the URL in
    ``extra`` (``url`` / ``web_app``) and use an empty callback; the keyboard builder in
    ``handlers/actions.py`` omits ``callback_data`` when a URL/web_app is present. URLs are already
    validated in ``parse_config`` (https/tg only), so a malformed target never reaches here.
    """
    bt = it.get("btype", "action")
    if bt == "link":
        extra["url"] = it.get("url", "")
        return "", extra
    if bt == "miniapp":
        from aiogram.types import WebAppInfo

        extra["web_app"] = WebAppInfo(url=it.get("url", ""))
        return "", extra
    if bt == "screen":
        return f"cabsub:{it['key']}", extra
    if bt == "back":
        return "nav:root", extra
    return custom_callback(it["action"]), extra


def _norm_icon(value: object) -> str | None:
    """A button icon is a numeric Telegram ``custom_emoji_id``; anything else is dropped."""
    s = str(value or "").strip()
    return s if s.isdigit() and 1 <= len(s) <= 24 else None


def _norm_color(value: object) -> str | None:
    """A button color is a hex (#RGB/#RRGGBB/#RRGGBBAA); the bot maps it to a Bot API style."""
    s = str(value or "").strip()
    return s if s.startswith("#") and len(s) in (4, 7, 9) else None


def _config_root(raw: str | None) -> tuple[object, int | None, str | None]:
    """Split ``CABINET_BUTTONS`` into (items-source, per_row, layout).

    Storage shapes, newest first:
      * ``{"per_row": N, "layout": "uniform"|"custom", "items": [...]}`` -- the current form;
        ``layout=="custom"`` means each item carries its own ``row``; otherwise buttons are
        chunked ``per_row`` wide;
      * a bare JSON list ``[...]`` -- older form, no layout (per_row/layout None);
      * legacy CSV of built-in keys -- returned as the raw string for the CSV branch.
    """
    text = (raw or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            pr = data.get("per_row")
            items = data.get("items")
            layout = data.get("layout")
            return (
                items if isinstance(items, list) else [],
                pr if isinstance(pr, int) else None,
                layout if layout in ("uniform", "custom") else None,
            )
        return [], None, None
    if text.startswith("["):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        return (data if isinstance(data, list) else []), None, None
    return text, None, None


def parse_config(raw: str | None) -> list[dict]:
    """``CABINET_BUTTONS`` -> normalized list of item dicts.

    Each item is ``{key, label, action, icon, color, enabled, custom}``. Unknown built-in keys,
    custom rows without a label/action, and malformed entries are dropped. An empty/absent config
    falls back to the full default catalogue so the cabinet is never left with no buttons at all.
    Accepts the object form, a bare JSON list, or legacy CSV (see ``_config_root``).
    """
    source, _, _ = _config_root(raw)
    items: list[dict] = []
    seen: set[str] = set()

    if isinstance(source, list):
        for it in source:
            if not isinstance(it, dict):
                continue
            key = str(it.get("key") or "").strip()
            if not key or key in seen:
                continue
            enabled = bool(it.get("enabled", True))
            icon = _norm_icon(it.get("icon"))
            color = _norm_color(it.get("color"))
            row = it.get("row")
            row = row if isinstance(row, int) else None
            if key in _BY_KEY:
                label = str(it.get("label") or "").strip() or _BY_KEY[key].label
                items.append({"key": key, "label": label, "action": None, "icon": icon,
                              "color": color, "enabled": enabled, "custom": False, "row": row})
                seen.add(key)
            else:
                label = str(it.get("label") or "").strip()
                if not label:
                    continue
                btype = str(it.get("btype") or "action").strip().lower()
                if btype not in ("action", "link", "miniapp", "screen", "back"):
                    btype = "action"
                action = ""
                url = ""
                stext = ""
                if btype == "action":
                    action = str(it.get("action") or "").strip().lower()
                    if not action:
                        continue
                elif btype in ("link", "miniapp"):
                    url = str(it.get("url") or "").strip()
                    # A half-typed link/miniapp button is dropped, never rendered — a bad URL
                    # would make the whole cabinet keyboard fail to send.
                    if not (url.startswith("https://") or url.startswith("tg://")):
                        continue
                elif btype == "screen":
                    stext = str(it.get("stext") or "")
                # ``back`` needs only a label.
                items.append({"key": key, "label": label, "action": action, "icon": icon,
                              "color": color, "enabled": enabled, "custom": True, "row": row,
                              "btype": btype, "url": url, "stext": stext})
                seen.add(key)
    else:
        # Legacy CSV of enabled built-in keys, in order.
        for token in str(source).split(","):
            k = token.strip().lower()
            if k in _BY_KEY and k not in seen:
                items.append({"key": k, "label": _BY_KEY[k].label, "action": None, "icon": None,
                              "color": None, "enabled": True, "custom": False, "row": None})
                seen.add(k)

    if not items:
        items = [{"key": b.key, "label": b.label, "action": None, "icon": None, "color": None,
                  "enabled": True, "custom": False, "row": None} for b in CABINET_BUTTONS]
    return items


def cabinet_columns(raw: str | None) -> int:
    """Buttons per row in the cabinet keyboard (owner-set); 2 by default, clamped to 1..3."""
    _, per_row, _ = _config_root(raw)
    if not isinstance(per_row, int):
        return 2
    return min(3, max(1, per_row))


def cabinet_mode(raw: str | None) -> str:
    """``"custom"`` when the owner arranged rows by hand, else ``"uniform"``."""
    _, _, layout = _config_root(raw)
    return "custom" if layout == "custom" else "uniform"


def cabinet_rows(raw: str | None, *, flags: dict[str, bool]) -> list[list[tuple[str, str, dict]]]:
    """Cabinet keyboard as physical rows of (label, callback, extra), honouring the layout mode.

    ``uniform`` chunks the enabled buttons ``per_row`` wide (default 2). ``custom`` groups them by
    each item's ``row`` (a new physical row starts when ``row`` changes), always capped at 3 per
    row to match the Bot API grid. Gating and premium-emoji/color handling mirror ``cabinet_buttons``.
    """
    from src.bot.keyboards import style_for_hex  # hex -> success/danger/primary, same as the menu

    _, per_row, layout = _config_root(raw)
    width = min(3, max(1, per_row)) if isinstance(per_row, int) else 2

    entries: list[tuple[str, str, dict, int | None]] = []
    for it in parse_config(raw):
        if not it["enabled"]:
            continue
        extra: dict = {}
        if it.get("icon"):
            extra["icon_custom_emoji_id"] = it["icon"]
        style = style_for_hex(it.get("color"))
        if style:
            extra["style"] = style
        if it["custom"]:
            cb, extra = _custom_target(it, extra)
            entries.append((it["label"], cb, extra, it.get("row")))
        else:
            b = _BY_KEY[it["key"]]
            if b.gate is not None and not flags.get(b.gate, True):
                continue
            entries.append((it["label"], b.callback, extra, it.get("row")))

    rows: list[list[tuple[str, str, dict]]] = []
    if layout == "custom":
        current: int | None = None
        started = False
        for label, cb, extra, r in entries:
            rr = r if isinstance(r, int) else 0
            if not started or rr != current or len(rows[-1]) >= 3:
                rows.append([])
                current = rr
                started = True
            rows[-1].append((label, cb, extra))
    else:
        for i in range(0, len(entries), width):
            rows.append([(lbl, cb, extra) for (lbl, cb, extra, _r) in entries[i:i + width]])
    return rows


def parse_cabinet_buttons(raw: str | None) -> list[str]:
    """Back-compat helper: ordered list of enabled built-in keys (custom buttons excluded)."""
    return [it["key"] for it in parse_config(raw) if it["enabled"] and not it["custom"]]


def cabinet_buttons(raw: str | None, *, flags: dict[str, bool]) -> list[tuple[str, str, dict]]:
    """(label, callback, extra) triples for the enabled cabinet buttons in owner order.

    ``extra`` holds the keyword args for ``InlineKeyboardButton`` beyond text/callback — currently
    ``icon_custom_emoji_id`` when the button carries a premium-emoji icon, else empty. Built-in
    buttons use the owner's label override (or the catalogue default) and are skipped when their
    feature flag (``flags`` maps a gate name -> on/off) is off. Custom buttons render their own
    label and point at ``act:<action>:0``.
    """
    from src.bot.keyboards import style_for_hex  # hex -> success/danger/primary, same as the menu

    out: list[tuple[str, str, dict]] = []
    for it in parse_config(raw):
        if not it["enabled"]:
            continue
        extra: dict = {}
        if it.get("icon"):
            extra["icon_custom_emoji_id"] = it["icon"]
        style = style_for_hex(it.get("color"))
        if style:
            extra["style"] = style
        if it["custom"]:
            cb, extra = _custom_target(it, extra)
            out.append((it["label"], cb, extra))
        else:
            b = _BY_KEY[it["key"]]
            if b.gate is not None and not flags.get(b.gate, True):
                continue
            out.append((it["label"], b.callback, extra))
    return out
