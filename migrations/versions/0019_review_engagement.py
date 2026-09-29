"""Review prompt preferences and aggregate engagement metrics.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_users", sa.Column("review_prompt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("usage_users", sa.Column("review_prompts_disabled", sa.Boolean(), nullable=False, server_default="0"))
    op.execute("UPDATE usage_users SET review_prompt_count = 1 WHERE review_prompted_at IS NOT NULL")
    op.add_column("usage_requests", sa.Column("review_prompt_kind", sa.String(16), nullable=True))
    op.create_table(
        "review_click_daily",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("source", sa.String(16), primary_key=True),
        sa.Column("clicks", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "published_review_snapshots",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("platform", sa.String(16), primary_key=True),
        sa.Column("ratings", sa.Integer(), nullable=False),
        sa.Column("reviews", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("published_review_snapshots")
    op.drop_table("review_click_daily")
    op.drop_column("usage_requests", "review_prompt_kind")
    op.drop_column("usage_users", "review_prompts_disabled")
    op.drop_column("usage_users", "review_prompt_count")
