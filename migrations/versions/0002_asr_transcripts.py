"""privacy-bounded ASR transcript corpus

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asr_transcripts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column("normalized_text", sa.String(255), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("confidence_percent", sa.SmallInteger(), nullable=False),
        sa.Column("candidate_count", sa.SmallInteger(), nullable=False),
        sa.Column("legal_move_count", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asr_transcripts_owner_key", "asr_transcripts", ["owner_key"])
    # Retention deletes by age, so the purge must not scan the whole corpus.
    op.create_index("ix_asr_transcripts_created_at", "asr_transcripts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_asr_transcripts_created_at", table_name="asr_transcripts")
    op.drop_index("ix_asr_transcripts_owner_key", table_name="asr_transcripts")
    op.drop_table("asr_transcripts")
