"""withdrawal requests (referral payouts)

Revision ID: b71f0d2c9ad1
Revises: a68a369a14c4
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b71f0d2c9ad1"
down_revision = "a68a369a14c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "withdrawal_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("details", sa.String(length=256), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "PAID", "REJECTED", name="withdrawalstatus", native_enum=False, length=16
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("admin_comment", sa.String(length=256), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_withdrawal_requests_user_id", "withdrawal_requests", ["user_id"])
    op.create_index("ix_withdrawal_requests_status", "withdrawal_requests", ["status"])


def downgrade() -> None:
    op.drop_table("withdrawal_requests")
