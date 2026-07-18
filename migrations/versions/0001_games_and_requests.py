"""games, move history, pending engine turns and Alice replay records

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "games",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("player_color", sa.String(5), nullable=False),
        sa.Column("initial_fen", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("engine_skill_level", sa.SmallInteger(), nullable=False),
        sa.Column("engine_move_time_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_games_owner_key", "games", ["owner_key"])

    op.create_table(
        "game_moves",
        sa.Column("game_id", sa.CHAR(36), nullable=False),
        sa.Column("ply", sa.Integer(), nullable=False),
        sa.Column("uci", sa.String(5), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_id", "ply"),
    )

    op.create_table(
        "pending_engine_turns",
        sa.Column("game_id", sa.CHAR(36), nullable=False),
        sa.Column("token", sa.CHAR(36), nullable=False),
        sa.Column("player_move_uci", sa.String(5), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_id"),
    )

    op.create_table(
        "request_replays",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column("game_id", sa.CHAR(36), nullable=True),
        sa.Column("response_payload", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "session_id", "message_id", name="uq_request_replays_key"),
    )


def downgrade() -> None:
    op.drop_table("request_replays")
    op.drop_table("pending_engine_turns")
    op.drop_table("game_moves")
    op.drop_index("ix_games_owner_key", table_name="games")
    op.drop_table("games")
