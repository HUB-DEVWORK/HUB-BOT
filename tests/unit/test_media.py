"""Banner/screen media: GIF/MP4 animate (send_animation), stills use send_photo."""

from __future__ import annotations

from aiogram.types import FSInputFile

from src.bot.media import is_animated, media_input


def test_is_animated_by_extension() -> None:
    assert is_animated("uploads/x.gif") is True
    assert is_animated("uploads/x.mp4") is True
    assert is_animated("https://cdn/site/clip.webm") is True
    assert is_animated("uploads/logo.png") is False
    assert is_animated("https://cdn/site/pic.jpg?v=2") is False  # query string ignored
    assert is_animated(None) is False


def test_is_animated_by_marker_fileid() -> None:
    # A Telegram animation file_id has no extension — /setbanner stores it with a marker.
    assert is_animated("animation:BAADAgADabc123") is True
    assert is_animated("AgACAgIAAxphoto_id") is False  # plain photo file_id


def test_media_input_strips_marker_and_wraps_local() -> None:
    assert media_input("animation:FILEID") == "FILEID"  # marker stripped for the send
    assert media_input("AgACphoto") == "AgACphoto"  # file_id passes through
    assert media_input("https://cdn/x.gif") == "https://cdn/x.gif"  # URL passes through
    fs = FSInputFile("/tmp/x.gif")
    assert media_input(fs) is fs  # already an input


def test_animation_fileid_input_is_animated_after_strip() -> None:
    ref = "animation:SOMEFILEID"
    assert is_animated(ref) is True
    assert media_input(ref) == "SOMEFILEID"
