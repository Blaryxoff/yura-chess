"""durable per-owner presentation preferences

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_preferences",
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column(
            "detail_level",
            sa.Enum("brief", "normal", "detailed", name="player_detail_level"),
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "pause_style",
            sa.Enum("normal", "extended", name="player_pause_style"),
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "notation_style",
            sa.Enum("full", "short", name="player_notation_style"),
            nullable=False,
            server_default="full",
        ),
        sa.Column(
            "board_orientation",
            sa.Enum("player", "white", "black", name="player_board_orientation"),
            nullable=False,
            server_default="player",
        ),
        sa.Column(
            "default_mode",
            sa.Enum("game", "training", name="player_default_mode"),
            nullable=False,
            server_default="game",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("owner_key"),
    )


def downgrade() -> None:
    op.drop_table("player_preferences")
