"""partners (reseller / affiliate accounts)

Revision ID: c1e4a8b2d6f9
Revises: b3d5f1a7c9e2
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1e4a8b2d6f9"
down_revision = "b3d5f1a7c9e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("markup_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_share_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turnover_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("earnings_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.UniqueConstraint("code", name="uq_partner_code"),
    )
    op.create_index("ix_partners_telegram_id", "partners", ["telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_partners_telegram_id", table_name="partners")
    op.drop_table("partners")
