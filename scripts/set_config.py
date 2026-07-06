"""Set bot-config registry values from the CLI (validated + type-coerced via the registry).

Usage: ``uv run python scripts/set_config.py KEY=value [KEY2=value2 ...]``
Example: ``uv run python scripts/set_config.py ADMIN_PANEL_URL=https://example.com/admin/``
"""

from __future__ import annotations

import asyncio
import sys

from src.core.logging import configure_logging, get_logger
from src.infrastructure.di import AppContainer

log = get_logger("set_config")


async def main(pairs: list[str]) -> int:
    configure_logging(level="INFO", json=False)
    changes: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            log.error("bad argument, expected KEY=value", arg=pair)
            return 2
        key, value = pair.split("=", 1)
        changes[key.strip()] = value
    if not changes:
        log.error("no KEY=value pairs given")
        return 2

    container = AppContainer.from_env()
    try:
        async with container.uow() as uow:
            applied = await container.bot_config.set_values(uow, changes)
            await uow.commit()
    finally:
        await container.aclose()
    log.info("config updated", keys=applied)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
