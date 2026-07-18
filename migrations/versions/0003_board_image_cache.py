"""yandex board image cache

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "board_image_cache",
        sa.Column("position_hash", sa.CHAR(64), nullable=False),
        sa.Column("image_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("position_hash"),
    )
    # Eviction picks by age of last use, so it must not scan the whole cache.
    op.create_index("ix_board_image_cache_last_used_at", "board_image_cache", ["last_used_at"])


def downgrade() -> None:
    op.drop_index("ix_board_image_cache_last_used_at", table_name="board_image_cache")
    op.drop_table("board_image_cache")
