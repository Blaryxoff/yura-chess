"""reclassify legacy ping monitors as test traffic

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Early uptime probes reached the Alice webhook as fresh `ping` sessions.
    # Reclassify only high-volume owners whose retained corpus contains nothing
    # but probe words and who never made a player move.
    op.execute(
        """
        UPDATE usage_users AS users
        JOIN (
            SELECT transcripts.owner_key
            FROM asr_transcripts AS transcripts
            GROUP BY transcripts.owner_key
            HAVING COUNT(*) >= 10
               AND SUM(transcripts.normalized_text NOT IN ('ping', 'test')) = 0
        ) AS monitors ON monitors.owner_key = users.owner_key
        SET users.traffic_source = 'test'
        WHERE NOT EXISTS (
            SELECT 1
            FROM games
            JOIN game_moves ON game_moves.game_id = games.id
            WHERE games.owner_key = users.owner_key
              AND game_moves.actor = 'player'
        )
        """
    )


def downgrade() -> None:
    # Traffic classification is durable evidence. Reverting the schema must not
    # turn known synthetic traffic back into real users.
    pass
