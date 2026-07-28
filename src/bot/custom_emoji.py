"""Custom (premium) emoji in owner-authored bot text.

Telegram renders a custom emoji inside an HTML message via
``<tg-emoji emoji-id="123">🙂</tg-emoji>`` — the child glyph is the fallback shown to clients
that can't display the custom one (and what survives if the tag is ever stripped by the safety
net). Bots may use any custom emoji by id, so the owner only needs an easy way to drop one into
a banner / screen / broadcast without hand-writing HTML that could break ``parse_mode=HTML``.

Owner syntax — works anywhere owner text is rendered as HTML (screens, banners, broadcasts)::

    [ce:5368324170671202286:🔥]

expands to ``<tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>``. The id is the numeric
``custom_emoji_id`` (forward the emoji to an id bot to read it); the part after the second colon
is the fallback glyph. Malformed tokens are left untouched. Custom emoji only render in message
TEXT, never on inline-keyboard buttons — that's a Telegram platform limit, not this helper's.
"""

from __future__ import annotations

import re
from html import escape

# [ce:<digits>:<fallback>] — id is digits only; fallback is 1..32 non-] chars (usually 1 glyph).
_TOKEN = re.compile(r"\[ce:(\d{1,24}):([^\]]{1,32})\]")


def apply_custom_emoji(text: str | None) -> str | None:
    """Expand ``[ce:<id>:<fallback>]`` tokens into ``<tg-emoji>`` tags for HTML parse_mode.

    A no-op when the text carries no token, so it is safe to call on every outgoing message. The
    fallback is HTML-escaped; the id is digits-only by construction. Never raises — on any error
    the original text is returned unchanged, so this helper can never break delivery.
    """
    if not text or "[ce:" not in text:
        return text
    try:
        return _TOKEN.sub(
            lambda m: f'<tg-emoji emoji-id="{m.group(1)}">{escape(m.group(2))}</tg-emoji>',
            text,
        )
    except Exception:
        return text
