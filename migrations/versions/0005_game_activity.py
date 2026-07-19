"""move activity metadata and unfinished-game lookup

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("last_player_move_at", sa.DateTime(), nullable=True))
    op.add_column("game_moves", sa.Column("actor", sa.String(6), nullable=True))
    op.add_column("game_moves", sa.Column("created_at", sa.DateTime(), nullable=True))

    # The starting FEN identifies which colour owns every existing ply, so the
    # backfill does not need to alter or replay the canonical UCI history.
    op.execute(
        """
        UPDATE game_moves AS move
        JOIN games AS game ON game.id = move.game_id
        SET move.actor = CASE
            WHEN (
                MOD(move.ply, 2) = 0
                AND (
                    (SUBSTRING_INDEX(SUBSTRING_INDEX(game.initial_fen, ' ', 2), ' ', -1) = 'w'
                     AND game.player_color = 'white')
                    OR
                    (SUBSTRING_INDEX(SUBSTRING_INDEX(game.initial_fen, ' ', 2), ' ', -1) = 'b'
                     AND game.player_color = 'black')
                )
            ) OR (
                MOD(move.ply, 2) = 1
                AND (
                    (SUBSTRING_INDEX(SUBSTRING_INDEX(game.initial_fen, ' ', 2), ' ', -1) = 'w'
                     AND game.player_color = 'black')
                    OR
                    (SUBSTRING_INDEX(SUBSTRING_INDEX(game.initial_fen, ' ', 2), ' ', -1) = 'b'
                     AND game.player_color = 'white')
                )
            ) THEN 'player'
            ELSE 'engine'
        END,
        move.created_at = game.updated_at
        """
    )
    op.alter_column("game_moves", "actor", existing_type=sa.String(6), nullable=False)
    op.alter_column(
        "game_moves",
        "created_at",
        existing_type=sa.DateTime(),
        nullable=False,
        server_default=sa.func.now(),
    )
    op.execute(
        """
        UPDATE games AS game
        SET game.last_player_move_at = (
            SELECT MAX(move.created_at)
            FROM game_moves AS move
            WHERE move.game_id = game.id AND move.actor = 'player'
        )
        """
    )
    op.create_index(
        "ix_games_owner_status_last_player_move",
        "games",
        ["owner_key", "status", "last_player_move_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_games_owner_status_last_player_move", table_name="games")
    op.drop_column("game_moves", "created_at")
    op.drop_column("game_moves", "actor")
    op.drop_column("games", "last_player_move_at")
