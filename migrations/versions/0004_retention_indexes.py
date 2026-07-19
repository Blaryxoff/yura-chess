"""retention indexes and explicit utf8mb4 tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "games",
    "game_moves",
    "pending_engine_turns",
    "request_replays",
    "asr_transcripts",
    "board_image_cache",
)


def upgrade() -> None:
    op.create_index("ix_request_replays_created_at", "request_replays", ["created_at"])
    op.create_index("ix_board_image_cache_created_at", "board_image_cache", ["created_at"])
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")


def downgrade() -> None:
    op.drop_index("ix_board_image_cache_created_at", table_name="board_image_cache")
    op.drop_index("ix_request_replays_created_at", table_name="request_replays")
