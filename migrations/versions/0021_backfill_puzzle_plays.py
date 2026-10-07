"""Restore puzzle plays evidenced by legacy attempt progress.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    legacy_attempts = (
        connection.execute(
            sa.text(
                "SELECT pa.owner_key, pa.puzzle_id, pa.updated_at FROM puzzle_attempts pa "
                "JOIN usage_users u ON u.owner_key = pa.owner_key "
                "WHERE pa.run_key IS NULL AND (pa.status = 'solved' OR pa.node > 0 OR pa.mistakes > 0)"
            )
        )
        .mappings()
        .all()
    )
    for attempt in legacy_attempts:
        run_key = str(uuid5(NAMESPACE_URL, f"yura-chess-legacy-play:{attempt['owner_key']}:{attempt['puzzle_id']}"))
        connection.execute(
            sa.text(
                "INSERT INTO usage_puzzle_plays (run_key, owner_key, created_at) "
                "VALUES (:run_key, :owner_key, :created_at) "
                "ON DUPLICATE KEY UPDATE run_key = run_key"
            ),
            {"run_key": run_key, "owner_key": attempt["owner_key"], "created_at": attempt["updated_at"]},
        )
        connection.execute(
            sa.text(
                "UPDATE puzzle_attempts SET run_key = :run_key "
                "WHERE owner_key = :owner_key AND puzzle_id = :puzzle_id AND run_key IS NULL"
            ),
            {"run_key": run_key, "owner_key": attempt["owner_key"], "puzzle_id": attempt["puzzle_id"]},
        )


def downgrade() -> None:
    pass
