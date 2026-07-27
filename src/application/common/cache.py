"""KeyValueCache protocol: the tiny seam to Redis for application services.

Members are declared as sync-returning-Awaitable (not ``async def``) and params are
positional-only: that's what lets ``redis.asyncio.Redis`` satisfy the protocol
structurally: its methods are typed ``-> ResponseT`` and name the key param ``name``.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class KeyValueCache(Protocol):
    """get/set with TTL: the only cache surface services are allowed to touch."""

    def get(self, key: str, /) -> Awaitable[Any]: ...

    def set(self, key: str, value: str, /, *, ex: int | None = None) -> Awaitable[Any]: ...
