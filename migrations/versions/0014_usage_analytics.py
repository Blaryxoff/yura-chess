"""durable privacy-safe usage analytics

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEST_SESSION_SQL = """session_id LIKE 'deployed-%'
    OR session_id LIKE 'first-%'
    OR session_id LIKE 'return-%'
    OR session_id LIKE 'e2e-%'
    OR session_id LIKE 'smoke-%'
    OR session_id LIKE 'test-%'"""


def upgrade() -> None:
    op.create_table(
        "usage_users",
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column("traffic_source", sa.Enum("real", "test", name="traffic_source"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("owner_key"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_usage_users_source_last_seen",
        "usage_users",
        ["traffic_source", "last_seen_at"],
    )
    op.create_table(
        "usage_requests",
        sa.Column("request_key", sa.CHAR(64), nullable=False),
        sa.Column("owner_key", sa.CHAR(64), nullable=False),
        sa.Column("session_key", sa.CHAR(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_key"], ["usage_users.owner_key"]),
        sa.PrimaryKeyConstraint("request_key"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_usage_requests_created_owner", "usage_requests", ["created_at", "owner_key"])
    op.create_index("ix_usage_requests_session_created", "usage_requests", ["session_key", "created_at"])

    op.execute(
        """
        INSERT INTO usage_users (owner_key, traffic_source, first_seen_at, last_seen_at)
        SELECT owner_key, 'real', MIN(created_at), MAX(updated_at)
        FROM games
        GROUP BY owner_key
        """
    )
    op.execute(
        f"""
        INSERT INTO usage_users (owner_key, traffic_source, first_seen_at, last_seen_at)
        SELECT owner_key,
               IF(MAX({_TEST_SESSION_SQL}) > 0, 'test', 'real'),
               MIN(created_at),
               MAX(created_at)
        FROM request_replays
        GROUP BY owner_key
        ON DUPLICATE KEY UPDATE
            traffic_source = IF(VALUES(traffic_source) = 'test', 'test', traffic_source),
            first_seen_at = LEAST(first_seen_at, VALUES(first_seen_at)),
            last_seen_at = GREATEST(last_seen_at, VALUES(last_seen_at))
        """
    )
    op.execute(
        """
        INSERT INTO usage_requests (request_key, owner_key, session_key, created_at)
        SELECT SHA2(CONCAT(skill_id, CHAR(0), session_id, CHAR(0), message_id), 256),
               owner_key,
               SHA2(CONCAT(skill_id, CHAR(0), session_id), 256),
               created_at
        FROM request_replays
        """
    )


def downgrade() -> None:
    op.drop_index("ix_usage_requests_session_created", table_name="usage_requests")
    op.drop_index("ix_usage_requests_created_owner", table_name="usage_requests")
    op.drop_table("usage_requests")
    op.drop_index("ix_usage_users_source_last_seen", table_name="usage_users")
    op.drop_table("usage_users")
