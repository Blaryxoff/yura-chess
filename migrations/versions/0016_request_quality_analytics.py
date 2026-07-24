"""add release-aware request quality analytics

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_requests",
        sa.Column("release_id", sa.String(length=128), server_default="unknown", nullable=False),
    )
    op.add_column("usage_requests", sa.Column("command_kind", sa.String(length=32), nullable=True))
    op.add_column("usage_requests", sa.Column("resolution_status", sa.String(length=16), nullable=True))
    op.add_column("usage_requests", sa.Column("routing_outcome", sa.String(length=24), nullable=True))
    op.add_column("asr_transcripts", sa.Column("request_key", sa.CHAR(length=64), nullable=True))
    op.create_index("ix_asr_transcripts_request_key", "asr_transcripts", ["request_key"])


def downgrade() -> None:
    op.drop_index("ix_asr_transcripts_request_key", table_name="asr_transcripts")
    op.drop_column("asr_transcripts", "request_key")
    op.drop_column("usage_requests", "routing_outcome")
    op.drop_column("usage_requests", "resolution_status")
    op.drop_column("usage_requests", "command_kind")
    op.drop_column("usage_requests", "release_id")
