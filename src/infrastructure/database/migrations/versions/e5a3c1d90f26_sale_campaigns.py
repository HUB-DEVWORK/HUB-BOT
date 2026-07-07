"""sale campaigns (month-start limited-quantity discounts)

Revision ID: e5a3c1d90f26
Revises: d7e2f9a1b4c8
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5a3c1d90f26"
down_revision = "d7e2f9a1b4c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sale_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=128), nullable=False, server_default="Скидка месяца"),
        sa.Column("discount_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("end_day", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_period", sa.String(length=7), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
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
    )


def downgrade() -> None:
    op.drop_table("sale_campaigns")
