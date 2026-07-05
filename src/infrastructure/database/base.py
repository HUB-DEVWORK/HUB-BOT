"""Declarative base, shared column types, and mixins.

Portability note: the base is Postgres-first (JSONB) but every custom type declares a
SQLite variant so the test-suite can run against in-memory aiosqlite without Postgres.
All datetimes are UTC-aware via :class:`AwareDateTime` (gotcha #17).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import BigInteger, DateTime, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import JSON


class AwareDateTime(TypeDecorator[dt.datetime]):
    """A ``DateTime(timezone=True)`` that guarantees UTC-aware values on the way in and out.

    Naive datetimes are assumed UTC and coerced; aware ones are converted to UTC. Prevents
    the silent-comparison bugs from mixing naive/aware datetimes.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)

    def process_result_value(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


# JSONB on Postgres, JSON on SQLite (tests). Use for snapshots/settings/arrays.
JsonB = JSONB().with_variant(JSON(), "sqlite")

# 64-bit ints for money (minor units), telegram ids, byte counts.
BigInt = BigInteger().with_variant(BigInteger(), "sqlite")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JsonB,
        list[Any]: JsonB,
    }


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the DB."""

    created_at: Mapped[dt.datetime] = mapped_column(
        AwareDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        AwareDateTime, server_default=func.now(), onupdate=utcnow, nullable=False
    )


class IntPk:
    """Auto-increment integer primary key."""

    id: Mapped[int] = mapped_column(primary_key=True)
