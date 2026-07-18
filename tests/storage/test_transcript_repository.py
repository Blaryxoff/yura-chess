"""The ASR corpus: what it may hold, and how long it may hold it."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from yura_chess.storage.models import AsrTranscriptRow
from yura_chess.storage.transcript_repository import TranscriptRepository
from yura_chess.voice.types import ResolutionStatus

OWNER = "a" * 64
OTHER_OWNER = "b" * 64

# Anything that would re-identify the speaker or reconstruct the raw request.
FORBIDDEN_COLUMNS = frozenset(
    {
        "audio",
        "audio_url",
        "access_token",
        "token",
        "payload",
        "raw_payload",
        "request_payload",
        "original_utterance",
        "user_id",
        "application_id",
        "session_id",
        "message_id",
    }
)


@pytest.fixture
def transcripts(session: Session) -> TranscriptRepository:
    return TranscriptRepository(session)


def test_schema_cannot_hold_audio_tokens_payloads_or_alice_identifiers() -> None:
    columns = {column.name for column in inspect(AsrTranscriptRow).columns}

    assert columns & FORBIDDEN_COLUMNS == set()
    assert columns == {
        "id",
        "owner_key",
        "normalized_text",
        "outcome",
        "confidence_percent",
        "candidate_count",
        "legal_move_count",
        "created_at",
    }


def test_records_only_the_normalised_utterance(session: Session, transcripts: TranscriptRepository) -> None:
    row = transcripts.record(
        OWNER,
        "пешка е два е четыре",
        ResolutionStatus.RESOLVED,
        confidence=0.85,
        candidate_count=1,
        legal_move_count=20,
    )
    session.commit()

    stored = session.get(AsrTranscriptRow, row.id)
    assert stored is not None
    assert stored.owner_key == OWNER
    assert stored.normalized_text == "пешка е два е четыре"
    assert stored.outcome == ResolutionStatus.RESOLVED.value
    assert stored.confidence_percent == 85
    assert stored.legal_move_count == 20


def test_text_is_clipped_to_the_configured_limit(session: Session) -> None:
    repository = TranscriptRepository(session, text_limit=32)

    row = repository.record(OWNER, "е" * 200, ResolutionStatus.UNMATCHED)
    session.commit()

    assert len(row.normalized_text) == 32


def test_purge_removes_only_rows_past_retention(session: Session, transcripts: TranscriptRepository) -> None:
    now = datetime(2026, 7, 18, 12, 0, 0)
    fresh = transcripts.record(OWNER, "конь эф три", ResolutionStatus.RESOLVED)
    stale = transcripts.record(OTHER_OWNER, "ладья дэ один", ResolutionStatus.AMBIGUOUS, candidate_count=2)
    stale.created_at = now - timedelta(days=31)
    fresh.created_at = now - timedelta(days=1)
    session.flush()

    removed = transcripts.purge_expired(now, retention_days=30)
    session.commit()

    assert removed == 1
    surviving = session.scalars(select(AsrTranscriptRow.id)).all()
    assert surviving == [fresh.id]
