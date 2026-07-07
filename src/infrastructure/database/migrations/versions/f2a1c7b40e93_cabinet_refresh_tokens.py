"""cabinet refresh tokens (web auth)

Revision ID: f2a1c7b40e93
Revises: e7a41c05d2b8
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a1c7b40e93"
down_revision = "e7a41c05d2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cabinet_refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_info", sa.String(length=256), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "ix_cabinet_refresh_tokens_token_hash",
        "cabinet_refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index("ix_cabinet_refresh_tokens_user_id", "cabinet_refresh_tokens", ["user_id"])
    op.create_index(
        "ix_cabinet_refresh_tokens_expires_at", "cabinet_refresh_tokens", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("cabinet_refresh_tokens")
