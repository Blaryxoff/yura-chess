"""puzzle difficulty profiles and attempts

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "puzzle_profiles",
        sa.Column("owner_key", sa.CHAR(64), primary_key=True),
        sa.Column(
            "bucket",
            sa.Enum("low", "medium", "high", name="puzzle_bucket"),
            nullable=False,
            server_default="medium",
        ),
        sa.Column("clean_streak", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("failure_streak", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
            nullable=False,
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_table(
        "puzzle_attempts",
        sa.Column("owner_key", sa.CHAR(64), primary_key=True),
        # The Lichess id of the packaged catalogue entry; the catalogue is a
        # shipped file rather than a table, so there is nothing to reference.
        sa.Column("puzzle_id", sa.String(16), primary_key=True),
        sa.Column("node", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("mistakes", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("hints", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("streak", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Enum("active", "solved", "failed", "abandoned", name="puzzle_attempt_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
            nullable=False,
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    # Resuming an unfinished puzzle after a new session looks up the owner's
    # latest attempt, independently of any unfinished game.
    op.create_index("ix_puzzle_attempts_updated_at", "puzzle_attempts", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_puzzle_attempts_updated_at", table_name="puzzle_attempts")
    op.drop_table("puzzle_attempts")
    op.drop_table("puzzle_profiles")
