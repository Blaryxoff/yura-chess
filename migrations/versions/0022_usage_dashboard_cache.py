"""Persist historical dashboard aggregates.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_dashboard_cache",
        sa.Column("source", sa.String(8), primary_key=True),
        sa.Column("period", sa.String(8), primary_key=True),
        sa.Column("reporting_day", sa.Date(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("totals", sa.JSON(), nullable=False),
        sa.Column("buckets", sa.JSON(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_usage_requests_owner_created", "usage_requests", ["owner_key", "created_at"])


def downgrade() -> None:
    owner_key = next(
        key
        for key in sa.inspect(op.get_bind()).get_foreign_keys("usage_requests")
        if key["constrained_columns"] == ["owner_key"]
    )
    op.drop_constraint(owner_key["name"], "usage_requests", type_="foreignkey")
    op.drop_index("ix_usage_requests_owner_created", table_name="usage_requests")
    op.create_foreign_key(
        owner_key["name"],
        "usage_requests",
        owner_key["referred_table"],
        owner_key["constrained_columns"],
        owner_key["referred_columns"],
        **owner_key.get("options", {}),
    )
    op.drop_table("usage_dashboard_cache")
