"""saved card autopay (YooKassa recurring charges)

Revision ID: e7a41c05d2b8
Revises: e4f2c9d17a55
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7a41c05d2b8"
down_revision = "e4f2c9d17a55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("saved_payment_method_id", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "users", sa.Column("saved_payment_method_title", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "subscriptions",
        sa.Column("autopay_card_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "subscriptions",
        sa.Column("autopay_card_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "autopay_card_attempted_at")
    op.drop_column("subscriptions", "autopay_card_enabled")
    op.drop_column("users", "saved_payment_method_title")
    op.drop_column("users", "saved_payment_method_id")
