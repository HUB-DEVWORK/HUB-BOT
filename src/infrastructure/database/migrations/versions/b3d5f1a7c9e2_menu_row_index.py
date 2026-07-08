"""menu node row_index (multiple buttons per row)

Revision ID: b3d5f1a7c9e2
Revises: a1b7c3d9e5f2
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3d5f1a7c9e2"
down_revision = "a1b7c3d9e5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "menu_nodes",
        sa.Column("row_index", sa.Integer(), nullable=False, server_default="0"),
    )
    # Existing menus keep their one-button-per-row look: put each button on its own row.
    op.execute("UPDATE menu_nodes SET row_index = order_index")


def downgrade() -> None:
    op.drop_column("menu_nodes", "row_index")
