"""reminder steps (hour-based subscription-expiry reminders)

Revision ID: c4d9e1f2a3b7
Revises: f2a1c7b40e93
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d9e1f2a3b7"
down_revision = "f2a1c7b40e93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminder_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hours_before", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=4096), nullable=False),
        sa.Column("button_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.UniqueConstraint("hours_before", name="uq_reminder_hours"),
    )


def downgrade() -> None:
    op.drop_table("reminder_steps")
