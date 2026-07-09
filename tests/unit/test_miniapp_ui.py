"""Mini-app UI-override validation: custom blocks, link-buttons, section visibility.

The ``ui`` JSONB is admin-authored, so the PATCH validator is the trust boundary —
it must drop junk (empty blocks, bad urls, unknown keys) and neutralize ``javascript:``.
"""

from __future__ import annotations

from src.web.routes.admin.miniapp import (
    MiniappPatch,
    _clean_blocks,
    _clean_buttons_extra,
    _clean_url,
)


def test_clean_url_allows_safe_schemes_and_rejects_the_rest() -> None:
    assert _clean_url("https://t.me/chan") == "https://t.me/chan"
    assert _clean_url("tg://resolve?domain=x") == "tg://resolve?domain=x"
    assert _clean_url("  https://a.com  ") == "https://a.com"  # trimmed
    assert _clean_url("javascript:alert(1)") is None  # XSS scheme dropped
    assert _clean_url("ftp://x") is None
    assert _clean_url("") is None
    assert _clean_url("https://a.com/" + "x" * 600) is None  # over 512 chars


def test_clean_blocks_drops_empty_and_sanitizes() -> None:
    out = _clean_blocks(
        [
            {"title": "Channel", "text": "join", "url": "https://t.me/c", "button_label": "Open"},
            {"title": "", "text": "", "button_label": ""},  # fully empty → dropped
            {"button_label": "Chat", "url": "javascript:evil"},  # bad url neutralized, label kept
        ]
    )
    assert len(out) == 2
    assert out[0]["url"] == "https://t.me/c"
    assert out[0]["screen"] == "home"  # default
    assert out[1]["url"] is None  # javascript stripped, block still valid (has a label)


def test_clean_blocks_caps_count() -> None:
    many = [{"title": f"b{i}"} for i in range(50)]
    assert len(_clean_blocks(many)) == 16


def test_clean_buttons_extra_requires_label_and_url() -> None:
    out = _clean_buttons_extra(
        [
            {"label": "Канал", "url": "https://t.me/c", "style": "ghost"},
            {"label": "no url"},  # missing url → dropped
            {"url": "https://x.com"},  # missing label → dropped
            {"label": "bad", "url": "data:text/html,x"},  # bad scheme → dropped
        ]
    )
    assert len(out) == 1
    assert out[0]["label"] == "Канал"
    assert out[0]["style"] == "ghost"
    assert out[0]["screen"] == "home"


def test_ui_shape_full_roundtrip() -> None:
    patch = MiniappPatch(
        ui={
            "scale": 999,  # clamped
            "sections": ["plans", "status", "bogus"],  # bogus filtered
            "hidden": ["referral", "nope"],  # nope filtered
            "buttons": {"renew": {"text": "Купить", "color": "#fff"}, "evil": {"text": "x"}},
            "blocks": [{"title": "Hi", "screen": "account"}],
            "buttons_extra": [{"label": "TG", "url": "https://t.me/x", "screen": "connect"}],
        }
    )
    ui = patch.ui
    assert ui is not None
    assert ui["scale"] == 115  # clamped to max
    assert ui["sections"] == ["plans", "status"]  # bogus removed, order kept
    assert ui["hidden"] == ["referral"]
    assert "evil" not in ui["buttons"] and ui["buttons"]["renew"]["text"] == "Купить"
    assert ui["blocks"][0]["screen"] == "account"
    assert ui["buttons_extra"][0]["screen"] == "connect"
