"""Pure-logic tests for the bot module system.

Covers :class:`ModuleManifest` (enable-key derivation, declared defaults) and
:meth:`ModuleSystem.resolve_enabled` (config gate + ``requires`` fixpoint drop),
driven with hand-built manifests and a minimal fake DI container. No real module
is imported and no dispatcher is run.
"""

from __future__ import annotations

from typing import Any

from src.bot.modules.api import DEFAULT_ROUTER_PRIORITY, ModuleManifest
from src.bot.modules.loader import ModuleSystem


# -- fakes ------------------------------------------------------------------
class _FakeUowCtx:
    """Async context manager standing in for ``container.uow()``."""

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeBotConfig:
    """Returns the enable flag per key; raises KeyError for unknown keys so the
    loader's except-branch (fall back to ``default_enabled``) can be exercised.
    """

    def __init__(self, flags: dict[str, bool]) -> None:
        self._flags = flags

    async def value(self, uow: object, key: str) -> bool:
        return self._flags[key]


class _FakeContainer:
    def __init__(self, flags: dict[str, bool]) -> None:
        self.bot_config = _FakeBotConfig(flags)

    def uow(self) -> _FakeUowCtx:
        return _FakeUowCtx()


def _manifest(
    name: str,
    *,
    default_enabled: bool = True,
    requires: tuple[str, ...] = (),
) -> ModuleManifest:
    return ModuleManifest(
        name=name,
        title_ru="RU title",
        title_en="EN title",
        default_enabled=default_enabled,
        requires=requires,
    )


def _system(*manifests: ModuleManifest) -> ModuleSystem:
    system = ModuleSystem()
    system._manifests = {m.name: m for m in manifests}
    return system


def _flags(system: ModuleSystem, **on: bool) -> dict[str, bool]:
    """Map every manifest's enable-key to a bool (default True unless overridden)."""
    return {m.enable_key(): on.get(name, True) for name, m in system._manifests.items()}


# -- ModuleManifest ---------------------------------------------------------
def test_enable_key_uppercases_name() -> None:
    assert _manifest("foo").enable_key() == "MODULE_FOO_ENABLED"


def test_enable_key_keeps_underscores_and_uppercases_mixed_case() -> None:
    assert _manifest("message_Cleaner").enable_key() == "MODULE_MESSAGE_CLEANER_ENABLED"


def test_manifest_defaults() -> None:
    m = _manifest("solo", default_enabled=False)
    assert m.default_enabled is False
    assert m.requires == ()
    assert m.version == "1.0.0"
    assert m.router_priority == DEFAULT_ROUTER_PRIORITY


def test_manifest_declared_fields_stick() -> None:
    m = ModuleManifest(
        name="dep",
        title_ru="RU",
        title_en="EN",
        version="2.5.0",
        router_priority=25,
        default_enabled=True,
        requires=("base",),
    )
    assert m.default_enabled is True
    assert m.requires == ("base",)
    assert m.version == "2.5.0"
    assert m.router_priority == 25


# -- resolve_enabled: config gate ------------------------------------------
async def test_all_enabled_with_deps_are_kept() -> None:
    system = _system(_manifest("a", requires=("b",)), _manifest("b"))
    enabled = await system.resolve_enabled(_FakeContainer(_flags(system)))
    assert enabled == {"a", "b"}
    assert system.enabled == {"a", "b"}


async def test_config_off_module_is_excluded() -> None:
    system = _system(_manifest("a"), _manifest("b"))
    enabled = await system.resolve_enabled(_FakeContainer(_flags(system, b=False)))
    assert enabled == {"a"}


async def test_missing_key_falls_back_to_default_enabled() -> None:
    # bot_config raises KeyError for every key -> loader uses default_enabled.
    system = _system(_manifest("on", default_enabled=True), _manifest("off", default_enabled=False))
    enabled = await system.resolve_enabled(_FakeContainer({}))
    assert enabled == {"on"}


# -- resolve_enabled: requires drop ----------------------------------------
async def test_module_dropped_when_required_dep_is_config_off() -> None:
    system = _system(
        _manifest("a", requires=("b",)),
        _manifest("b"),
        _manifest("c"),  # independent, must survive
    )
    enabled = await system.resolve_enabled(_FakeContainer(_flags(system, b=False)))
    assert enabled == {"c"}


async def test_requires_chain_collapses_to_fixpoint() -> None:
    # A -> B -> C, and C is disabled: A and B must both drop; D is independent.
    system = _system(
        _manifest("a", requires=("b",)),
        _manifest("b", requires=("c",)),
        _manifest("c"),
        _manifest("d"),
    )
    enabled = await system.resolve_enabled(_FakeContainer(_flags(system, c=False)))
    assert enabled == {"d"}


async def test_full_chain_kept_when_all_on() -> None:
    system = _system(
        _manifest("a", requires=("b",)),
        _manifest("b", requires=("c",)),
        _manifest("c"),
    )
    enabled = await system.resolve_enabled(_FakeContainer(_flags(system)))
    assert enabled == {"a", "b", "c"}


async def test_resolve_returns_fresh_copy_not_internal_set() -> None:
    system = _system(_manifest("a"))
    returned = await system.resolve_enabled(_FakeContainer(_flags(system)))
    returned.add("bogus")
    assert system.enabled == {"a"}  # mutating the return value must not leak in


async def test_enabled_checker_reflects_resolution() -> None:
    # resolve_enabled installs a predicate on the hook bus (n in self._enabled)
    # so hooks owned by disabled modules get skipped. Assert it was wired.
    from src.bot import hooks as hook_bus

    system = _system(_manifest("a"), _manifest("b"))
    await system.resolve_enabled(_FakeContainer(_flags(system, b=False)))
    checker: Any = hook_bus._is_enabled
    assert checker("a") is True
    assert checker("b") is False
