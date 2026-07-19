"""analysis checkpoints of valued player moves

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_checkpoints",
        sa.Column("game_id", sa.CHAR(36), sa.ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ply", sa.Integer(), primary_key=True),
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column("position_hash", sa.CHAR(64), nullable=False),
        sa.Column("score_before_centipawns", sa.Integer(), nullable=True),
        sa.Column("score_before_mate_in", sa.SmallInteger(), nullable=True),
        sa.Column("score_after_centipawns", sa.Integer(), nullable=True),
        sa.Column("score_after_mate_in", sa.SmallInteger(), nullable=True),
        sa.Column("centipawn_loss", sa.Integer(), nullable=False),
        sa.Column("engine_depth", sa.SmallInteger(), nullable=False),
        sa.Column("engine_search_time_ms", sa.Integer(), nullable=False),
        sa.Column("engine_skill_level", sa.SmallInteger(), nullable=False),
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
    op.create_index("ix_analysis_checkpoints_owner_key", "analysis_checkpoints", ["owner_key"])


def downgrade() -> None:
    op.drop_index("ix_analysis_checkpoints_owner_key", table_name="analysis_checkpoints")
    op.drop_table("analysis_checkpoints")
