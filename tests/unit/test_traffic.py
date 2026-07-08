"""TrafficSnapshot DAO: one row per (subscription, day), newest-first series."""

from __future__ import annotations

from src.infrastructure.database.uow import UnitOfWork


async def test_upsert_is_idempotent_per_day_and_series_desc(uow: UnitOfWork) -> None:
    async with uow:
        await uow.traffic.upsert(1, "2026-07-01", 100)
        await uow.traffic.upsert(1, "2026-07-02", 250)
        await uow.traffic.upsert(1, "2026-07-01", 150)  # same day -> update, no duplicate row
        await uow.commit()
        rows = await uow.traffic.series(1, limit=10)
    assert [(r.day, r.used_bytes) for r in rows] == [("2026-07-02", 250), ("2026-07-01", 150)]
