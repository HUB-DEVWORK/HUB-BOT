"""E2E smoke test against the configured Remnawave panel.

Run: ``make smoke`` (or ``python scripts/smoke.py``). Requires REMNAWAVE__* in .env.
Proves the connection profile, auth, version probe and user provisioning work end-to-end.
Creates a throwaway panel user and deletes it. Does NOT touch the database.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys

from src.application.services.ids import generate_short_id
from src.core.config import get_settings
from src.core.constants import MIN_REMNAWAVE_VERSION
from src.core.logging import configure_logging, get_logger
from src.infrastructure.remnawave.client import RemnawaveHttpClient
from src.infrastructure.remnawave.connection import build_profile

log = get_logger("smoke")


async def main() -> int:
    settings = get_settings()
    configure_logging(level="INFO", json=False)
    if not settings.remnawave.base_url or not settings.remnawave.token:
        log.error("set REMNAWAVE__BASE_URL and REMNAWAVE__TOKEN in .env first")
        return 2

    profile = build_profile(settings.remnawave)
    log.info(
        "connecting",
        base_url=profile.base_url,
        verify=profile.verify,
        local=settings.remnawave.is_local,
    )
    client = RemnawaveHttpClient.from_profile(profile)
    created_uuid = None
    try:
        version = await client.get_version()
        log.info("panel_version", version=version.raw, tuple=version.tuple)
        if version.tuple < MIN_REMNAWAVE_VERSION:
            log.warning("panel below supported minimum", minimum=MIN_REMNAWAVE_VERSION)

        squads = await client.get_internal_squads()
        log.info("internal_squads", count=len(squads))

        short_id = generate_short_id()
        from src.application.services.remnawave import RemnawaveService

        svc = RemnawaveService(client)
        spec = svc.build_spec(
            short_id=short_id,
            telegram_id=None,
            expire_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
            traffic_limit_bytes=0,
            device_limit=1,
            internal_squads=tuple(str(s.uuid) for s in squads[:1]),
        )
        panel_user = await client.create_user(spec)
        created_uuid = panel_user.uuid
        log.info("created_user", uuid=str(panel_user.uuid), sub_url=panel_user.subscription_url)

        fetched = await client.get_user_by_uuid(panel_user.uuid)
        log.info("fetched_user", ok=fetched is not None)
    except Exception:
        log.error("smoke failed", exc_info=True)
        return 1
    finally:
        if created_uuid is not None:
            try:
                await client.delete_user(created_uuid)
                log.info("cleaned_up", uuid=str(created_uuid))
            except Exception:
                log.warning(
                    "cleanup failed — delete this panel user manually", uuid=str(created_uuid)
                )
        await client.aclose()

    log.info("smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
