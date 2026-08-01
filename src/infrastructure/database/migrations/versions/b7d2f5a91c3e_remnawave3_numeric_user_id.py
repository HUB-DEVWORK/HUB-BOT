"""subscriptions.remnawave_id — numeric panel user id (Remnawave >=3.0)

Revision ID: b7d2f5a91c3e
Revises: b8e1f4a2c7d9
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d2f5a91c3e"
down_revision = "b8e1f4a2c7d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remnawave 3.0 dropped the user uuid and keys users by a numeric id. Existing
    # subscriptions keep their remnawave_uuid (still valid on 2.x panels); the numeric
    # id is backfilled lazily — by the first 3.0 webhook or client resolve for each sub.
    op.add_column("subscriptions", sa.Column("remnawave_id", sa.BigInteger(), nullable=True))
    # Same hot webhook lookup as ix_subscriptions_remnawave_uuid, for 3.0 payloads.
    op.create_index(
        "ix_subscriptions_remnawave_id",
        "subscriptions",
        ["remnawave_id"],
        postgresql_where=sa.text("remnawave_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_remnawave_id", table_name="subscriptions")
    op.drop_column("subscriptions", "remnawave_id")
