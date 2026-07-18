"""Privacy-bounded storage for the ASR corpus.

The repository is the only writer of `asr_transcripts`, and it accepts only
already-normalised text. Retention is enforced by `purge_expired`, so a row that
is never purged is a scheduling bug, not a schema one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from yura_chess.storage.models import TRANSCRIPT_TEXT_LENGTH, AsrTranscriptRow
from yura_chess.voice.types import ResolutionStatus


class TranscriptRepository:
    def __init__(self, session: Session, text_limit: int = TRANSCRIPT_TEXT_LENGTH) -> None:
        self._session = session
        self._text_limit = min(text_limit, TRANSCRIPT_TEXT_LENGTH)

    def record(
        self,
        owner_key: str,
        normalized_text: str,
        outcome: ResolutionStatus | str,
        confidence: float = 0.0,
        candidate_count: int = 0,
        legal_move_count: int = 0,
    ) -> AsrTranscriptRow:
        row = AsrTranscriptRow(
            owner_key=owner_key,
            normalized_text=normalized_text[: self._text_limit],
            outcome=str(outcome),
            confidence_percent=round(max(0.0, min(confidence, 1.0)) * 100),
            candidate_count=candidate_count,
            legal_move_count=legal_move_count,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def purge_expired(self, now: datetime, retention_days: int) -> int:
        """Delete rows older than the retention window; returns how many went."""
        cutoff = now - timedelta(days=retention_days)
        removed = self._session.query(AsrTranscriptRow).filter(AsrTranscriptRow.created_at < cutoff).delete()
        self._session.flush()
        return removed
