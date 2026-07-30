"""add the durable game-sound preference

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "player_preferences",
        sa.Column("sounds_enabled", sa.Boolean(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("player_preferences", "sounds_enabled")
