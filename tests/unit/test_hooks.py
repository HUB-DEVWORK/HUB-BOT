"""Module hook bus (src/bot/hooks.py): registration, run_hooks fan-out, ownership."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import src.bot.hooks as hooks_mod
from src.bot.hooks import (
    owner,
    register_hook,
    registered_hooks,
    run_hooks,
    set_enabled_checker,
    unregister_module_hooks,
)


@pytest.fixture(autouse=True)
def _reset_bus() -> Iterator[None]:
    """Isolate every test: empty the hook registry and restore the enabled checker."""
    hooks_mod._hooks.clear()
    saved_checker = hooks_mod._is_enabled
    yield
    hooks_mod._hooks.clear()
    set_enabled_checker(saved_checker)


def _module_hook(fn: Any, module: str) -> Any:
    """Give ``fn`` a synthetic ``__module__`` so ``owner()`` attributes it to a module."""
    fn.__module__ = module
    return fn


async def test_register_direct_call_returns_func_and_runs() -> None:
    def h() -> str:
        return "core"

    assert register_hook("evt", h) is h
    assert await run_hooks("evt") == ["core"]


async def test_register_as_decorator() -> None:
    @register_hook("evt")
    def h() -> str:
        return "deco"

    assert await run_hooks("evt") == ["deco"]


async def test_unknown_hook_name_returns_empty() -> None:
    assert await run_hooks("never-registered") == []


async def test_sync_and_async_hooks_both_run_in_order() -> None:
    def sync_h() -> str:
        return "sync"

    async def async_h() -> str:
        return "async"

    register_hook("evt", sync_h)
    register_hook("evt", async_h)
    assert await run_hooks("evt") == ["sync", "async"]


async def test_only_truthy_results_are_collected() -> None:
    register_hook("evt", lambda: "keep")
    register_hook("evt", lambda: None)
    register_hook("evt", lambda: 0)
    register_hook("evt", lambda: "")
    register_hook("evt", lambda: [])
    register_hook("evt", lambda: ["x"])
    assert await run_hooks("evt") == ["keep", ["x"]]


async def test_kwargs_are_passed_to_hooks() -> None:
    def double(value: int) -> int:
        return value * 2

    register_hook("evt", double)
    assert await run_hooks("evt", value=21) == [42]


async def test_raising_hook_is_swallowed_and_others_still_run() -> None:
    def sync_boom() -> str:
        raise RuntimeError("sync kaboom")

    async def async_boom() -> str:
        raise ValueError("async kaboom")

    def ok() -> str:
        return "ok"

    register_hook("evt", sync_boom)
    register_hook("evt", async_boom)
    register_hook("evt", ok)

    # run_hooks never propagates a hook failure; the healthy hook still contributes.
    assert await run_hooks("evt") == ["ok"]


async def test_disabled_module_hook_is_skipped_but_core_runs() -> None:
    def core_h() -> str:
        return "core"

    def demo_h() -> str:
        return "demo"

    _module_hook(demo_h, "src.bot.modules.demo.hooks")
    register_hook("evt", core_h)
    register_hook("evt", demo_h)

    set_enabled_checker(lambda name: name != "demo")

    assert await run_hooks("evt") == ["core"]


async def test_require_enabled_false_bypasses_the_checker() -> None:
    def demo_h() -> str:
        return "demo"

    _module_hook(demo_h, "src.bot.modules.demo.hooks")
    register_hook("evt", demo_h)

    set_enabled_checker(lambda _name: False)

    assert await run_hooks("evt", require_enabled=False) == ["demo"]


async def test_enabled_module_hook_runs() -> None:
    def demo_h() -> str:
        return "demo"

    _module_hook(demo_h, "src.bot.modules.demo.hooks")
    register_hook("evt", demo_h)

    set_enabled_checker(lambda _name: True)

    assert await run_hooks("evt") == ["demo"]


def test_owner_resolves_module_and_core() -> None:
    def core_h() -> None:
        return None

    def demo_h() -> None:
        return None

    _module_hook(demo_h, "src.bot.modules.demo.hooks")
    assert owner(core_h) is None
    assert owner(demo_h) == "demo"


def test_registered_hooks_introspection_labels_core() -> None:
    def core_h() -> None:
        return None

    def demo_h() -> None:
        return None

    _module_hook(demo_h, "src.bot.modules.demo.hooks")
    register_hook("evt", core_h)
    register_hook("evt", demo_h)

    assert registered_hooks() == {"evt": ["core", "demo"]}


async def test_unregister_module_hooks_drops_only_that_module() -> None:
    def core_h() -> str:
        return "core"

    def demo_h() -> str:
        return "demo"

    _module_hook(demo_h, "src.bot.modules.demo.hooks")
    register_hook("evt", core_h)
    register_hook("evt", demo_h)

    unregister_module_hooks("demo")

    assert registered_hooks() == {"evt": ["core"]}
    assert await run_hooks("evt") == ["core"]
