"""render(): {placeholder} substitution for notification templates."""

from __future__ import annotations

from src.web.routes.admin.notifications import NOTIFICATION_EVENTS, render


def test_render_substitutes_known_and_keeps_unknown() -> None:
    assert render("Привет, {name}! Баланс {balance}", name="Ян") == "Привет, Ян! Баланс {balance}"


def test_render_no_placeholders_is_identity() -> None:
    assert render("нет плейсхолдеров") == "нет плейсхолдеров"


def test_events_are_unique_and_nonempty() -> None:
    events = [e for e, _t, _d, _p in NOTIFICATION_EVENTS]
    assert len(events) == len(set(events))
    for _e, title, default, _ph in NOTIFICATION_EVENTS:
        assert title.strip() and default.strip()
