"""review cursor of a finished game

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_reviews",
        sa.Column("game_id", sa.CHAR(36), sa.ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column(
            "section",
            sa.Enum("summary", "turning_point", "mistakes", "moves", name="game_review_section"),
            nullable=False,
            server_default="summary",
        ),
        sa.Column("ply", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
            nullable=False,
        ),
        # Revision 0004 pinned `games` to this collation in every environment,
        # and a foreign key requires the referencing column to share it exactly;
        # the server default alone would not (MariaDB 11.4 defaults to uca1400).
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_game_reviews_owner_key", "game_reviews", ["owner_key"])
    # Resuming a review after a new session looks up the owner's latest cursor.
    op.create_index("ix_game_reviews_updated_at", "game_reviews", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_game_reviews_updated_at", table_name="game_reviews")
    op.drop_index("ix_game_reviews_owner_key", table_name="game_reviews")
    op.drop_table("game_reviews")
