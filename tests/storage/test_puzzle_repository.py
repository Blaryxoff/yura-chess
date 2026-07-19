"""Puzzle profiles and attempts: difficulty, progress, completion and isolation."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.domain.puzzle import (
    DEFAULT_PUZZLE_BUCKET,
    PuzzleAttemptStatus,
    PuzzleBucket,
    PuzzleProfile,
    bucket_for_rating,
    catalogue,
)
from yura_chess.storage.models import PuzzleAttemptRow, PuzzleProfileRow
from yura_chess.storage.puzzle_repository import (
    InvalidPuzzleAttemptError,
    PuzzleAttemptRevisionConflictError,
    PuzzleRepository,
    UnknownPuzzleAttemptError,
)

OWNER = "a" * 64
OTHER_OWNER = "b" * 64
PUZZLE = "000Pw"
OTHER_PUZZLE = "000Zo"


@pytest.fixture
def puzzles(session: Session) -> PuzzleRepository:
    return PuzzleRepository(session)


def test_the_shipped_catalogue_agrees_with_the_bucket_boundaries() -> None:
    entries = catalogue()

    assert entries
    assert all(entry.bucket is bucket_for_rating(entry.rating) for entry in entries)


@pytest.mark.parametrize(
    ("rating", "bucket"),
    [(600, PuzzleBucket.LOW), (1400, PuzzleBucket.LOW), (1401, PuzzleBucket.MEDIUM), (1800, PuzzleBucket.MEDIUM)],
)
def test_the_bucket_boundaries_are_inclusive(rating: int, bucket: PuzzleBucket) -> None:
    assert bucket_for_rating(rating) is bucket
    assert bucket_for_rating(1801) is PuzzleBucket.HIGH


def test_difficulty_never_leaves_the_catalogue() -> None:
    assert PuzzleBucket.HIGH.harder() is PuzzleBucket.HIGH
    assert PuzzleBucket.LOW.easier() is PuzzleBucket.LOW
    assert PuzzleBucket.MEDIUM.harder() is PuzzleBucket.HIGH
    assert PuzzleBucket.MEDIUM.easier() is PuzzleBucket.LOW


def test_the_column_defaults_are_the_domain_defaults(session: Session, puzzles: PuzzleRepository) -> None:
    session.add(PuzzleProfileRow(owner_key=OWNER))
    session.flush()
    session.expire_all()

    profile = puzzles.load_profile(OWNER)

    assert (profile.bucket, profile.clean_streak, profile.failure_streak) == (DEFAULT_PUZZLE_BUCKET, 0, 0)


def test_an_owner_without_a_profile_starts_at_medium(
    session: Session,
    puzzles: PuzzleRepository,
) -> None:
    profile = puzzles.load_profile(OWNER)

    assert profile.bucket is PuzzleBucket.MEDIUM
    assert session.scalars(select(PuzzleProfileRow)).all() == []


def test_a_saved_profile_replaces_itself(session: Session, puzzles: PuzzleRepository) -> None:
    puzzles.save_profile(PuzzleProfile(OWNER, PuzzleBucket.HIGH, clean_streak=2))
    saved = puzzles.save_profile(PuzzleProfile(OWNER, PuzzleBucket.LOW, failure_streak=1))

    assert (saved.bucket, saved.clean_streak, saved.failure_streak) == (PuzzleBucket.LOW, 0, 1)
    assert len(session.scalars(select(PuzzleProfileRow)).all()) == 1


def test_a_new_attempt_starts_at_the_first_node(puzzles: PuzzleRepository) -> None:
    attempt = puzzles.start_attempt(OWNER, PUZZLE)

    assert attempt.status is PuzzleAttemptStatus.ACTIVE
    assert (attempt.node, attempt.mistakes, attempt.hints, attempt.streak, attempt.revision) == (0, 0, 0, 0, 1)


def test_starting_twice_resumes_one_attempt(session: Session, puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)
    puzzles.advance(OWNER, PUZZLE, started.revision, node=2, mistakes=1, hints=1)

    resumed = puzzles.start_attempt(OWNER, PUZZLE)

    assert (resumed.node, resumed.mistakes, resumed.hints) == (2, 1, 1)
    assert len(session.scalars(select(PuzzleAttemptRow)).all()) == 1


def test_starting_another_puzzle_abandons_the_unfinished_one(puzzles: PuzzleRepository) -> None:
    puzzles.start_attempt(OWNER, PUZZLE)

    puzzles.start_attempt(OWNER, OTHER_PUZZLE)

    abandoned = puzzles.find_attempt(OWNER, PUZZLE)
    assert abandoned is not None
    assert abandoned.status is PuzzleAttemptStatus.ABANDONED
    assert puzzles.find_active_attempt(OWNER) is not None
    assert puzzles.find_active_attempt(OWNER).puzzle_id == OTHER_PUZZLE


def test_a_finished_puzzle_starts_again_from_the_beginning(puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)
    advanced = puzzles.advance(OWNER, PUZZLE, started.revision, node=3, mistakes=2, hints=1)
    puzzles.finish_attempt(
        OWNER,
        PUZZLE,
        advanced.revision,
        PuzzleAttemptStatus.SOLVED,
        PuzzleProfile(OWNER, PuzzleBucket.MEDIUM, clean_streak=1),
    )

    restarted = puzzles.start_attempt(OWNER, PUZZLE)

    assert restarted.status is PuzzleAttemptStatus.ACTIVE
    assert (restarted.node, restarted.mistakes, restarted.hints) == (0, 0, 0)


def test_a_never_attempted_puzzle_is_not_an_error(puzzles: PuzzleRepository) -> None:
    assert puzzles.find_attempt(OWNER, PUZZLE) is None
    assert puzzles.find_active_attempt(OWNER) is None


def test_progress_is_absolute(puzzles: PuzzleRepository) -> None:
    """A replayed request asks for the node the attempt already holds."""
    started = puzzles.start_attempt(OWNER, PUZZLE)

    advanced = puzzles.advance(OWNER, PUZZLE, started.revision, node=2, mistakes=0, hints=1)
    replayed = puzzles.advance(OWNER, PUZZLE, started.revision, node=2, mistakes=0, hints=1)

    assert replayed == advanced
    assert replayed.revision == started.revision + 1


@pytest.mark.parametrize(("node", "mistakes", "hints"), [(-1, 0, 0), (0, -1, 0), (0, 0, -1)])
def test_negative_progress_is_rejected(
    puzzles: PuzzleRepository,
    node: int,
    mistakes: int,
    hints: int,
) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)

    with pytest.raises(InvalidPuzzleAttemptError):
        puzzles.advance(OWNER, PUZZLE, started.revision, node=node, mistakes=mistakes, hints=hints)

    assert puzzles.find_attempt(OWNER, PUZZLE) == started


def test_a_stale_revision_is_rejected(puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)
    puzzles.advance(OWNER, PUZZLE, started.revision, node=2, mistakes=0, hints=0)

    with pytest.raises(PuzzleAttemptRevisionConflictError):
        puzzles.advance(OWNER, PUZZLE, started.revision, node=4, mistakes=0, hints=0)


def test_an_unknown_attempt_cannot_be_advanced(puzzles: PuzzleRepository) -> None:
    with pytest.raises(UnknownPuzzleAttemptError):
        puzzles.advance(OWNER, PUZZLE, 1, node=1, mistakes=0, hints=0)


def test_completion_stores_the_attempt_and_its_difficulty_together(puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)

    attempt, profile = puzzles.finish_attempt(
        OWNER,
        PUZZLE,
        started.revision,
        PuzzleAttemptStatus.SOLVED,
        PuzzleProfile(OWNER, PuzzleBucket.HIGH, clean_streak=3),
    )

    assert attempt.status is PuzzleAttemptStatus.SOLVED
    assert attempt.streak == 3
    assert profile.bucket is PuzzleBucket.HIGH
    assert puzzles.load_profile(OWNER).clean_streak == 3


def test_replaying_completion_does_not_count_the_result_twice(puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)
    puzzles.finish_attempt(
        OWNER,
        PUZZLE,
        started.revision,
        PuzzleAttemptStatus.SOLVED,
        PuzzleProfile(OWNER, PuzzleBucket.MEDIUM, clean_streak=3),
    )

    attempt, profile = puzzles.finish_attempt(
        OWNER,
        PUZZLE,
        started.revision,
        PuzzleAttemptStatus.SOLVED,
        PuzzleProfile(OWNER, PuzzleBucket.HIGH, clean_streak=0),
    )

    assert (attempt.streak, profile.bucket, profile.clean_streak) == (3, PuzzleBucket.MEDIUM, 3)


def test_an_abandoned_attempt_leaves_no_active_puzzle(puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)

    puzzles.finish_attempt(
        OWNER,
        PUZZLE,
        started.revision,
        PuzzleAttemptStatus.ABANDONED,
        PuzzleProfile(OWNER),
    )

    assert puzzles.find_active_attempt(OWNER) is None


def test_an_attempt_cannot_be_finished_as_active(puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)

    with pytest.raises(InvalidPuzzleAttemptError):
        puzzles.finish_attempt(OWNER, PUZZLE, started.revision, PuzzleAttemptStatus.ACTIVE, PuzzleProfile(OWNER))

    assert puzzles.find_attempt(OWNER, PUZZLE) == started


def test_one_owner_cannot_read_anothers_puzzles(puzzles: PuzzleRepository) -> None:
    puzzles.start_attempt(OWNER, PUZZLE)
    puzzles.save_profile(PuzzleProfile(OWNER, PuzzleBucket.HIGH, clean_streak=3))

    assert puzzles.find_attempt(OTHER_OWNER, PUZZLE) is None
    assert puzzles.find_active_attempt(OTHER_OWNER) is None
    assert puzzles.load_profile(OTHER_OWNER) == PuzzleProfile(OTHER_OWNER)


def test_a_foreign_attempt_cannot_be_advanced(puzzles: PuzzleRepository) -> None:
    started = puzzles.start_attempt(OWNER, PUZZLE)

    with pytest.raises(UnknownPuzzleAttemptError):
        puzzles.advance(OTHER_OWNER, PUZZLE, started.revision, node=2, mistakes=0, hints=0)


def test_starting_a_puzzle_leaves_another_owners_attempt_running(puzzles: PuzzleRepository) -> None:
    puzzles.start_attempt(OTHER_OWNER, PUZZLE)

    puzzles.start_attempt(OWNER, OTHER_PUZZLE)

    still_running = puzzles.find_active_attempt(OTHER_OWNER)
    assert still_running is not None
    assert still_running.puzzle_id == PUZZLE


def test_an_attempt_survives_a_new_session(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as writing:
        repository = PuzzleRepository(writing)
        started = repository.start_attempt(OWNER, PUZZLE)
        repository.advance(OWNER, PUZZLE, started.revision, node=2, mistakes=1, hints=0)
        repository.save_profile(PuzzleProfile(OWNER, PuzzleBucket.LOW, failure_streak=2))
        writing.commit()

    with session_factory() as reading:
        repository = PuzzleRepository(reading)
        resumed = repository.find_active_attempt(OWNER)
        profile = repository.load_profile(OWNER)

    assert resumed is not None
    assert (resumed.puzzle_id, resumed.node, resumed.mistakes) == (PUZZLE, 2, 1)
    assert (profile.bucket, profile.failure_streak) == (PuzzleBucket.LOW, 2)


def test_a_concurrent_start_is_taken_over(session: Session, puzzles: PuzzleRepository) -> None:
    """An earlier request already opened this attempt: the second one resumes it."""
    session.add(
        PuzzleAttemptRow(
            owner_key=OWNER,
            puzzle_id=PUZZLE,
            node=2,
            mistakes=1,
            hints=0,
            streak=0,
            status=PuzzleAttemptStatus.ACTIVE.value,
            revision=3,
        )
    )
    session.flush()

    started = puzzles.start_attempt(OWNER, PUZZLE)

    assert (started.node, started.mistakes, started.revision) == (2, 1, 3)
    assert len(session.scalars(select(PuzzleAttemptRow)).all()) == 1
