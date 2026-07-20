"""deterministic resume indexes and database-managed timestamps

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPDATED_TABLES = (
    "games",
    "player_preferences",
    "analysis_checkpoints",
    "game_reviews",
    "puzzle_profiles",
    "puzzle_attempts",
)


def upgrade() -> None:
    op.drop_index("ix_games_owner_status_last_player_move", table_name="games")
    op.create_index(
        "ix_games_owner_status_last_player_move",
        "games",
        ["owner_key", "status", "last_player_move_at", "created_at", "id"],
    )
    op.create_index(
        "ix_game_reviews_owner_updated",
        "game_reviews",
        ["owner_key", "updated_at", "created_at", "game_id"],
    )
    op.create_index(
        "ix_puzzle_attempts_owner_status_updated",
        "puzzle_attempts",
        ["owner_key", "status", "updated_at", "created_at", "puzzle_id"],
    )
    for table in _UPDATED_TABLES:
        op.execute(
            f"ALTER TABLE {table} MODIFY updated_at DATETIME NOT NULL "
            "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
        )


def downgrade() -> None:
    for table in _UPDATED_TABLES:
        op.execute(f"ALTER TABLE {table} MODIFY updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP")
    op.drop_index("ix_puzzle_attempts_owner_status_updated", table_name="puzzle_attempts")
    op.drop_index("ix_game_reviews_owner_updated", table_name="game_reviews")
    op.drop_index("ix_games_owner_status_last_player_move", table_name="games")
    op.create_index(
        "ix_games_owner_status_last_player_move",
        "games",
        ["owner_key", "status", "last_player_move_at"],
    )
