"""Test doubles for the external adapters (panel, event bus)."""

from __future__ import annotations

from tests.fakes.event_bus import RecordingEventBus
from tests.fakes.panel import FakeRemnawaveClient

__all__ = ["FakeRemnawaveClient", "RecordingEventBus"]
