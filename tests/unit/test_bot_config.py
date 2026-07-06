"""BotConfigService: registry merge, coercion, secrets, hot-reload cache."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.application.services.bot_config import _MASK, BotConfigError, BotConfigService
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.payments.crypto import SecretBox


@pytest.fixture
def service() -> BotConfigService:
    return BotConfigService(SecretBox(Fernet.generate_key().decode()))


async def test_defaults_without_overrides(uow: UnitOfWork, service: BotConfigService) -> None:
    async with uow:
        assert await service.value(uow, "TRIAL_ENABLED") is True
        assert await service.value(uow, "TRIAL_DURATION_DAYS") == 3


async def test_set_and_reload(uow: UnitOfWork, service: BotConfigService) -> None:
    async with uow:
        written = await service.set_values(uow, {"TRIAL_DURATION_DAYS": "14"})
        await uow.commit()
    assert written == ["TRIAL_DURATION_DAYS"]
    async with uow:
        # int coercion from string + cache invalidation
        assert await service.value(uow, "TRIAL_DURATION_DAYS") == 14


async def test_bool_coercion(uow: UnitOfWork, service: BotConfigService) -> None:
    async with uow:
        await service.set_values(uow, {"TRIAL_ENABLED": "false"})
        assert await service.value(uow, "TRIAL_ENABLED") is False
        await service.set_values(uow, {"TRIAL_ENABLED": "1"})
        assert await service.value(uow, "TRIAL_ENABLED") is True


async def test_unknown_key_rejected(uow: UnitOfWork, service: BotConfigService) -> None:
    async with uow:
        with pytest.raises(BotConfigError):
            await service.set_values(uow, {"NO_SUCH_KEY": 1})
        with pytest.raises(BotConfigError):
            await service.value(uow, "NO_SUCH_KEY")


async def test_secret_encrypted_and_masked(uow: UnitOfWork, service: BotConfigService) -> None:
    async with uow:
        await service.set_values(uow, {"NALOGO_PASSWORD": "hunter2"})
        await uow.commit()
    async with uow:
        # at rest: fernet token, not plaintext
        row = await uow.bot_config.find_one(key="NALOGO_PASSWORD")
        assert row is not None
        assert row.value != "hunter2"
        assert str(row.value).startswith("gAAAA")
        # effective value decrypts
        assert await service.value(uow, "NALOGO_PASSWORD") == "hunter2"
        # listing masks it
        rows = await service.listing(uow)
        secret_row = next(r for r in rows if r["key"] == "NALOGO_PASSWORD")
        assert secret_row["value"] == _MASK


async def test_masked_roundtrip_is_noop(uow: UnitOfWork, service: BotConfigService) -> None:
    async with uow:
        await service.set_values(uow, {"NALOGO_PASSWORD": "hunter2"})
        # UI echoes the mask back on save-all: must NOT overwrite the secret
        written = await service.set_values(uow, {"NALOGO_PASSWORD": _MASK})
        assert written == []
        assert await service.value(uow, "NALOGO_PASSWORD") == "hunter2"


async def test_reset_restores_default(uow: UnitOfWork, service: BotConfigService) -> None:
    async with uow:
        await service.set_values(uow, {"TRIAL_DURATION_DAYS": 30})
        await service.reset(uow, ["TRIAL_DURATION_DAYS"])
        assert await service.value(uow, "TRIAL_DURATION_DAYS") == 3
