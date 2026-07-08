"""blacklist (permanently banned Telegram ids)

Revision ID: a1b7c3d9e5f2
Revises: f6b4d2e08a13
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b7c3d9e5f2"
down_revision = "f6b4d2e08a13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blacklist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("telegram_id", name="uq_blacklist_tg"),
    )
    op.create_index("ix_blacklist_telegram_id", "blacklist", ["telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_blacklist_telegram_id", table_name="blacklist")
    op.drop_table("blacklist")
