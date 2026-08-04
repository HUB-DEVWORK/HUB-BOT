"""broadcast_templates table

Revision ID: c3f8a1d6b204
Revises: b7d2f5a91c3e
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3f8a1d6b204"
down_revision = "b7d2f5a91c3e"
branch_labels = None
depends_on = None

# JSONB on Postgres, plain JSON elsewhere (mirrors src/.../base.py JsonB).
_JSONB = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # Saved broadcast composer presets (screen 07): reusable text + button/emoji config.
    op.create_table(
        "broadcast_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("text", sa.String(length=4096), nullable=False, server_default=""),
        sa.Column("extras", _JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_broadcast_templates_name"),
    )


def downgrade() -> None:
    op.drop_table("broadcast_templates")
