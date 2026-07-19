"""Owner-scoped storage for puzzle difficulty and puzzle attempts.

The catalogue itself is a packaged file, so nothing is stored about a puzzle
beyond its id: a row here only says how far one owner has come through one
solution line, and how hard their puzzles should be.

Reading a profile is total — an owner who never solved anything gets the
documented starting bucket without a row being written. Progress through a line
is written as absolute values, so a re-applied request that asks for the node the
attempt already holds changes nothing and does not bump the revision; a genuinely
concurrent writer is told to reload instead. Finishing an attempt writes the
attempt and its profile in one flush, and asking twice does not count the result
twice.

No game is touched from here, and no Alice identifier is representable: the
pseudonymous owner key is the only subject of a row.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yura_chess.domain.puzzle import (
    PuzzleAttempt,
    PuzzleAttemptStatus,
    PuzzleBucket,
    PuzzleProfile,
)
from yura_chess.storage.models import PuzzleAttemptRow, PuzzleProfileRow


class UnknownPuzzleAttemptError(LookupError):
    """No attempt at this puzzle belongs to this owner."""


class PuzzleAttemptRevisionConflictError(RuntimeError):
    """The attempt changed between read and write; the caller must reload."""


class InvalidPuzzleAttemptError(ValueError):
    """An attempt never has negative progress, and never finishes as active."""


def _to_profile(row: PuzzleProfileRow) -> PuzzleProfile:
    return PuzzleProfile(
        owner_key=row.owner_key,
        bucket=PuzzleBucket(row.bucket),
        clean_streak=row.clean_streak,
        failure_streak=row.failure_streak,
    )


def _to_attempt(row: PuzzleAttemptRow) -> PuzzleAttempt:
    return PuzzleAttempt(
        owner_key=row.owner_key,
        puzzle_id=row.puzzle_id,
        node=row.node,
        mistakes=row.mistakes,
        hints=row.hints,
        streak=row.streak,
        status=PuzzleAttemptStatus(row.status),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PuzzleRepository:
    """Thin data-access layer bound to one short transaction (one Session)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_profile(self, owner_key: str) -> PuzzleProfile:
        row = self._find_profile(owner_key)
        if row is None:
            return PuzzleProfile(owner_key=owner_key)
        return _to_profile(row)

    def save_profile(self, profile: PuzzleProfile) -> PuzzleProfile:
        """Store the whole profile for its owner, creating the row if needed."""
        row = self._upsert_profile(profile)
        self._session.flush()
        return _to_profile(row)

    def start_attempt(self, owner_key: str, puzzle_id: str) -> PuzzleAttempt:
        """Open an attempt at this puzzle, or resume the one already open.

        An owner works on one puzzle at a time: an attempt at a different puzzle
        that is still running is abandoned rather than left to be resumed later.
        A puzzle that was already finished starts again from the beginning.
        """
        row = self._find_attempt(owner_key, puzzle_id)
        self._abandon_others(owner_key, puzzle_id)
        if row is None:
            row = self._insert_attempt(owner_key, puzzle_id)
        elif PuzzleAttemptStatus(row.status).is_finished:
            self._reset(row)
        self._session.flush()
        return _to_attempt(row)

    def find_attempt(self, owner_key: str, puzzle_id: str) -> PuzzleAttempt | None:
        """A puzzle that was never attempted is not an error."""
        row = self._find_attempt(owner_key, puzzle_id)
        return _to_attempt(row) if row is not None else None

    def find_active_attempt(self, owner_key: str) -> PuzzleAttempt | None:
        """The unfinished puzzle this owner left behind, if there is one."""
        statement = (
            select(PuzzleAttemptRow)
            .where(
                PuzzleAttemptRow.owner_key == owner_key,
                PuzzleAttemptRow.status == PuzzleAttemptStatus.ACTIVE.value,
            )
            .order_by(PuzzleAttemptRow.updated_at.desc(), PuzzleAttemptRow.created_at.desc())
            .limit(1)
        )
        row = self._session.scalars(statement).one_or_none()
        return _to_attempt(row) if row is not None else None

    def advance(
        self,
        owner_key: str,
        puzzle_id: str,
        expected_revision: int,
        node: int,
        mistakes: int,
        hints: int,
    ) -> PuzzleAttempt:
        """Record absolute progress through the solution line.

        The values are absolute rather than increments, so a re-applied request
        that asks for the progress the attempt already holds leaves it untouched
        — including its revision, which is what makes a replay safe.
        """
        if node < 0 or mistakes < 0 or hints < 0:
            raise InvalidPuzzleAttemptError(f"progress {node}/{mistakes}/{hints} is negative")
        row = self._load_attempt(owner_key, puzzle_id)
        if (row.node, row.mistakes, row.hints) == (node, mistakes, hints):
            return _to_attempt(row)
        self._require_revision(row, expected_revision)
        row.node = node
        row.mistakes = mistakes
        row.hints = hints
        row.revision += 1
        self._session.flush()
        return _to_attempt(row)

    def finish_attempt(
        self,
        owner_key: str,
        puzzle_id: str,
        expected_revision: int,
        status: PuzzleAttemptStatus,
        profile: PuzzleProfile,
    ) -> tuple[PuzzleAttempt, PuzzleProfile]:
        """Close the attempt and store the difficulty it leaves behind, together.

        The caller decides what the streaks and the bucket become; storing them
        in the same flush as the closed attempt is what keeps a result from being
        counted without being recorded, or twice.
        """
        if not status.is_finished:
            raise InvalidPuzzleAttemptError("an attempt cannot be finished as active")
        row = self._load_attempt(owner_key, puzzle_id)
        if PuzzleAttemptStatus(row.status) is status:
            # The same result was already recorded; counting it again would move
            # the bucket a second time.
            return _to_attempt(row), self.load_profile(owner_key)
        self._require_revision(row, expected_revision)
        row.status = status.value
        row.streak = profile.clean_streak
        row.revision += 1
        stored = self._upsert_profile(profile)
        self._session.flush()
        return _to_attempt(row), _to_profile(stored)

    def _abandon_others(self, owner_key: str, puzzle_id: str) -> None:
        statement = select(PuzzleAttemptRow).where(
            PuzzleAttemptRow.owner_key == owner_key,
            PuzzleAttemptRow.puzzle_id != puzzle_id,
            PuzzleAttemptRow.status == PuzzleAttemptStatus.ACTIVE.value,
        )
        for row in self._session.scalars(statement):
            row.status = PuzzleAttemptStatus.ABANDONED.value
            row.revision += 1

    @staticmethod
    def _reset(row: PuzzleAttemptRow) -> None:
        row.node = 0
        row.mistakes = 0
        row.hints = 0
        row.streak = 0
        row.status = PuzzleAttemptStatus.ACTIVE.value
        row.revision += 1

    def _insert_attempt(self, owner_key: str, puzzle_id: str) -> PuzzleAttemptRow:
        row = PuzzleAttemptRow(
            owner_key=owner_key,
            puzzle_id=puzzle_id,
            node=0,
            mistakes=0,
            hints=0,
            streak=0,
            status=PuzzleAttemptStatus.ACTIVE.value,
            revision=1,
        )
        try:
            # A savepoint keeps a lost insert race from discarding the caller's transaction.
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            # A concurrent request already opened this attempt; the re-read must
            # lock, because a plain SELECT would reuse a snapshot predating that
            # commit.
            concurrent = self._find_attempt(owner_key, puzzle_id, for_update=True)
            if concurrent is None:
                raise
            return concurrent
        return row

    def _upsert_profile(self, profile: PuzzleProfile) -> PuzzleProfileRow:
        row = self._find_profile(profile.owner_key)
        if row is not None:
            self._apply_profile(row, profile)
            return row
        row = PuzzleProfileRow(owner_key=profile.owner_key)
        self._apply_profile(row, profile)
        try:
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            concurrent = self._find_profile(profile.owner_key, for_update=True)
            if concurrent is None:
                raise
            self._apply_profile(concurrent, profile)
            return concurrent
        return row

    def _find_profile(self, owner_key: str, for_update: bool = False) -> PuzzleProfileRow | None:
        statement = select(PuzzleProfileRow).where(PuzzleProfileRow.owner_key == owner_key)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalars(statement).one_or_none()

    def _find_attempt(self, owner_key: str, puzzle_id: str, for_update: bool = False) -> PuzzleAttemptRow | None:
        statement = select(PuzzleAttemptRow).where(
            PuzzleAttemptRow.owner_key == owner_key,
            PuzzleAttemptRow.puzzle_id == puzzle_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalars(statement).one_or_none()

    def _load_attempt(self, owner_key: str, puzzle_id: str) -> PuzzleAttemptRow:
        # A locking read serialises concurrent writers on the same attempt: the
        # second one waits, then sees the bumped revision and is rejected.
        row = self._find_attempt(owner_key, puzzle_id, for_update=True)
        if row is None:
            raise UnknownPuzzleAttemptError(f"attempt at puzzle {puzzle_id} is not available for this owner")
        return row

    @staticmethod
    def _require_revision(row: PuzzleAttemptRow, expected_revision: int) -> None:
        if row.revision != expected_revision:
            raise PuzzleAttemptRevisionConflictError(
                f"attempt at puzzle {row.puzzle_id} is at revision {row.revision}, expected {expected_revision}"
            )

    @staticmethod
    def _apply_profile(row: PuzzleProfileRow, profile: PuzzleProfile) -> None:
        row.bucket = profile.bucket.value
        row.clean_streak = profile.clean_streak
        row.failure_streak = profile.failure_streak
