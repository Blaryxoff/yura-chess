"""case-sensitive Lichess puzzle ids

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lichess ids are case-sensitive: the shipped catalogue contains both
    # 003Jb and 003jb, which must remain distinct for the same owner.
    op.alter_column(
        "puzzle_attempts",
        "puzzle_id",
        existing_type=sa.String(16),
        type_=sa.String(16, collation="utf8mb4_bin"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "puzzle_attempts",
        "puzzle_id",
        existing_type=sa.String(16, collation="utf8mb4_bin"),
        type_=sa.String(16, collation="utf8mb4_unicode_ci"),
        existing_nullable=False,
    )
