"""notification templates (owner-editable per-event message texts)

Revision ID: d7e2f9a1b4c8
Revises: c4d9e1f2a3b7
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d7e2f9a1b4c8"
down_revision = "c4d9e1f2a3b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("text", sa.String(length=4096), nullable=False),
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
        sa.UniqueConstraint("event", name="uq_notification_event"),
    )


def downgrade() -> None:
    op.drop_table("notification_templates")
