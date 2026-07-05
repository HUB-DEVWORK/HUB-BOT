"""Web dependencies: access the AppContainer stored on app.state."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from src.infrastructure.di import AppContainer


def get_container(request: Request) -> AppContainer:
    container: Any = request.app.state.container
    return container
