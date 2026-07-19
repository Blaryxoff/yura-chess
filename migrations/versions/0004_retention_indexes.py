"""retention indexes and explicit utf8mb4 tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _create_index_unless_present(inspector, "request_replays", "ix_request_replays_created_at", ["created_at"])
    _create_index_unless_present(inspector, "board_image_cache", "ix_board_image_cache_created_at", ["created_at"])

    foreign_keys: list[tuple[str, dict[str, Any]]] = []
    for table in _TABLES:
        foreign_keys.extend(
            (table, key) for key in inspector.get_foreign_keys(table) if key["referred_table"] in _TABLES
        )
    for table, key in foreign_keys:
        op.drop_constraint(key["name"], table, type_="foreignkey")

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")

    for table, key in foreign_keys:
        options = key.get("options") or {}
        op.create_foreign_key(
            key["name"],
            table,
            key["referred_table"],
            key["constrained_columns"],
            key["referred_columns"],
            ondelete=options.get("ondelete"),
            onupdate=options.get("onupdate"),
        )


def downgrade() -> None:
    op.drop_index("ix_board_image_cache_created_at", table_name="board_image_cache")
    op.drop_index("ix_request_replays_created_at", table_name="request_replays")


def _create_index_unless_present(
    inspector: sa.Inspector,
    table: str,
    name: str,
    columns: list[str],
) -> None:
    if name not in {index["name"] for index in inspector.get_indexes(table)}:
        op.create_index(name, table, columns)
