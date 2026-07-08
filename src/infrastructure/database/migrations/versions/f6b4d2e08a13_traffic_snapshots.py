"""traffic snapshots (daily per-subscription usage for the mini-app graph)

Revision ID: f6b4d2e08a13
Revises: e5a3c1d90f26
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6b4d2e08a13"
down_revision = "e5a3c1d90f26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traffic_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
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
        sa.UniqueConstraint("subscription_id", "day", name="uq_traffic_sub_day"),
    )
    op.create_index(
        "ix_traffic_snapshots_subscription_id", "traffic_snapshots", ["subscription_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_traffic_snapshots_subscription_id", table_name="traffic_snapshots")
    op.drop_table("traffic_snapshots")
