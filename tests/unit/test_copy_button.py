"""copy_button: native tap-to-copy button (Bot API 8.0) with the 256-char guard.

Users mistook the plain <code> link for a broken/error string and gave up; a real copy
button is unmistakable. It must degrade to None (keep the <code> fallback) rather than
crash the send when the payload is empty or over Telegram's limit.
"""

from __future__ import annotations

from src.bot.keyboards import copy_button


def test_copy_button_builds_native_copy_text() -> None:
    btn = copy_button("📋 Скопировать ссылку", "https://sub.example/u/abc")
    assert btn is not None
    assert btn.copy_text is not None
    assert btn.copy_text.text == "https://sub.example/u/abc"
    assert btn.callback_data is None and btn.url is None  # a copy button, not a link/nav


def test_copy_button_none_for_empty_payload() -> None:
    assert copy_button("copy", "") is None


def test_copy_button_none_when_over_telegram_limit() -> None:
    # Telegram rejects a copy_text payload longer than 256 chars; caller keeps <code>.
    assert copy_button("copy", "x" * 257) is None
    assert copy_button("copy", "x" * 256) is not None
