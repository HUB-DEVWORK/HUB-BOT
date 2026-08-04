"""users.bot_blocked_at — when the user last blocked/stopped the bot

Revision ID: d5b1e93c47af
Revises: c3f8a1d6b204
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5b1e93c47af"
down_revision = "c3f8a1d6b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Timestamp of the last 403 (user blocked/stopped the bot); powers the "blocked the bot"
    # stat and lets broadcasts skip dead chats. Cleared on the user's next interaction.
    # NULL for everyone on upgrade, so no backfill — the flag fills in as sends fail.
    op.add_column("users", sa.Column("bot_blocked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "bot_blocked_at")
