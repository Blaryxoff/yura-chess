"""game mode and per-position hint stage

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column(
            "mode",
            sa.Enum("game", "training", name="game_mode"),
            nullable=False,
            server_default="game",
        ),
    )
    op.add_column(
        "games",
        sa.Column("hint_stage", sa.SmallInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("games", "hint_stage")
    op.drop_column("games", "mode")
