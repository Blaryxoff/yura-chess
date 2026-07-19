"""Cache complete Alice responses for delivery-level idempotency.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("request_replays", sa.Column("alice_response_payload", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("request_replays", "alice_response_payload")
