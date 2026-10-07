"""Record each puzzle run's first submitted answer.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("puzzle_attempts", sa.Column("run_key", sa.CHAR(36), nullable=True))
    op.create_table(
        "usage_puzzle_plays",
        sa.Column("run_key", sa.CHAR(36), primary_key=True),
        sa.Column("owner_key", sa.CHAR(64), sa.ForeignKey("usage_users.owner_key"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_usage_puzzle_plays_created_owner", "usage_puzzle_plays", ["created_at", "owner_key"])


def downgrade() -> None:
    op.drop_index("ix_usage_puzzle_plays_created_owner", table_name="usage_puzzle_plays")
    op.drop_table("usage_puzzle_plays")
    op.drop_column("puzzle_attempts", "run_key")
