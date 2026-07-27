"""Owner ``[ce:<id>:<fallback>]`` tokens expand into ``<tg-emoji>`` tags for HTML parse_mode."""

from __future__ import annotations

from src.bot.custom_emoji import apply_custom_emoji


def test_single_token_expands_to_tg_emoji_tag() -> None:
    out = apply_custom_emoji("[ce:5368324170671202286:🔥]")
    # Exact tag shape the code emits: emoji-id carries the numeric id, child is the fallback glyph.
    assert out == '<tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>'


def test_multiple_tokens_all_expand() -> None:
    out = apply_custom_emoji("a [ce:1:🔥] b [ce:22:😀] c")
    assert out == (
        'a <tg-emoji emoji-id="1">🔥</tg-emoji> b <tg-emoji emoji-id="22">😀</tg-emoji> c'
    )


def test_text_without_token_is_unchanged() -> None:
    text = "plain text, no token here"
    assert apply_custom_emoji(text) == text


def test_malformed_non_numeric_id_is_left_untouched() -> None:
    # id must be digits (regex \d{1,24}); a non-numeric id does not match.
    text = "[ce:abc:🔥]"
    assert apply_custom_emoji(text) == text


def test_malformed_missing_fallback_part_is_left_untouched() -> None:
    # No second colon + fallback, so nothing matches even though "[ce:" is present.
    text = "[ce:123]"
    assert apply_custom_emoji(text) == text


def test_fallback_glyph_is_html_escaped() -> None:
    # A special char in the fallback is escaped via html.escape before landing inside the tag.
    out = apply_custom_emoji("[ce:1:<3]")
    assert out == '<tg-emoji emoji-id="1">&lt;3</tg-emoji>'


def test_none_input_returns_none() -> None:
    assert apply_custom_emoji(None) is None
