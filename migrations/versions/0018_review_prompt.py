"""remember the one-time catalogue review prompt

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_users", sa.Column("review_prompted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_users", "review_prompted_at")
