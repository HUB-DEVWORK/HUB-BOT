"""subscription grace window (спасательный круг)

Revision ID: b8e1f4a2c7d9
Revises: a9c3e7f14b28
Create Date: 2026-07-16

Идемпотентна: на боевой БД (Lazeyka) колонки уже созданы прежней порт-версией
поверх 1.6.0 — здесь миграция перецеплена на голову 1.9.0 (a9c3e7f14b28) и
использует IF NOT EXISTS, чтобы `upgrade head` прошёл и на уже-мигрированной БД,
и на свежей установке.
"""

from __future__ import annotations

from alembic import op

revision = "b8e1f4a2c7d9"
down_revision = "a9c3e7f14b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS grace_until TIMESTAMPTZ NULL")
    op.execute(
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS grace_started_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_subscriptions_grace_until ON subscriptions (grace_until)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_subscriptions_grace_until")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS grace_started_at")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS grace_until")
