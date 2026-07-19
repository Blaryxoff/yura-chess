"""Owner-scoped storage for the cursor of a game review.

Only a finished game can be reviewed, and only by the owner it belongs to, so a
foreign or still running game is rejected before any row is written. The cursor
is always set to absolute values under the review's own revision: a re-applied
request leaves it where it already is, while a genuinely concurrent writer is
told to reload instead of overwriting a cursor it never saw.

The reviewed game itself is never touched here — a review reads history, it does
not extend it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yura_chess.domain.game import GameStatus
from yura_chess.domain.review import DEFAULT_REVIEW_SECTION, GameReview, ReviewSection
from yura_chess.storage.models import GameReviewRow, GameRow


class UnknownReviewGameError(LookupError):
    """No game with this id belongs to this owner, so it cannot be reviewed."""


class GameNotFinishedError(RuntimeError):
    """The game is still being played; only a finished game can be reviewed."""


class ReviewRevisionConflictError(RuntimeError):
    """The review changed between read and write; the caller must reload."""


class InvalidReviewCursorError(ValueError):
    """A cursor never points before the start of a section."""


def _to_review(row: GameReviewRow) -> GameReview:
    return GameReview(
        game_id=row.game_id,
        owner_key=row.owner_key,
        section=ReviewSection(row.section),
        ply=row.ply,
        page=row.page,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ReviewRepository:
    """Thin data-access layer bound to one short transaction (one Session)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start(self, game_id: str, owner_key: str) -> GameReview:
        """Open the review of a finished game, or return the one already open."""
        self._require_own_finished_game(game_id, owner_key)
        row = self._find_row(game_id, owner_key)
        if row is None:
            row = self._insert(game_id, owner_key)
        return _to_review(row)

    def find(self, game_id: str, owner_key: str) -> GameReview | None:
        """A game that was never reviewed is not an error."""
        row = self._find_row(game_id, owner_key)
        return _to_review(row) if row is not None else None

    def find_latest(self, owner_key: str) -> GameReview | None:
        """The review this owner touched last, so a new session can resume it."""
        statement = (
            select(GameReviewRow)
            .where(GameReviewRow.owner_key == owner_key)
            .order_by(GameReviewRow.updated_at.desc(), GameReviewRow.created_at.desc())
            .limit(1)
        )
        row = self._session.scalars(statement).one_or_none()
        return _to_review(row) if row is not None else None

    def set_cursor(
        self,
        game_id: str,
        owner_key: str,
        expected_revision: int,
        section: ReviewSection,
        ply: int = 0,
        page: int = 0,
    ) -> GameReview:
        """Move the cursor to an absolute place in the review."""
        if ply < 0 or page < 0:
            raise InvalidReviewCursorError(f"cursor ply {ply} / page {page} is negative")
        row = self._load_row(game_id, owner_key, expected_revision)
        row.section = section.value
        row.ply = ply
        row.page = page
        row.revision += 1
        self._session.flush()
        return _to_review(row)

    def finish(self, game_id: str, owner_key: str) -> bool:
        """Forget a finished review; asking twice is not an error."""
        row = self._find_row(game_id, owner_key)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def _insert(self, game_id: str, owner_key: str) -> GameReviewRow:
        row = GameReviewRow(
            game_id=game_id,
            owner_key=owner_key,
            section=DEFAULT_REVIEW_SECTION.value,
            ply=0,
            page=0,
            revision=1,
        )
        try:
            # A savepoint keeps a lost insert race from discarding the caller's transaction.
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            # A concurrent request already opened this review; the re-read must
            # lock, because a plain SELECT would reuse a snapshot predating that
            # commit.
            concurrent = self._find_row(game_id, owner_key, for_update=True)
            if concurrent is None:
                raise
            return concurrent
        self._session.flush()
        return row

    def _find_row(self, game_id: str, owner_key: str, for_update: bool = False) -> GameReviewRow | None:
        statement = select(GameReviewRow).where(
            GameReviewRow.game_id == game_id,
            GameReviewRow.owner_key == owner_key,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalars(statement).one_or_none()

    def _load_row(self, game_id: str, owner_key: str, expected_revision: int) -> GameReviewRow:
        # A locking read serialises concurrent writers on the same review: the
        # second one waits, then sees the bumped revision and is rejected.
        row = self._find_row(game_id, owner_key, for_update=True)
        if row is None:
            raise UnknownReviewGameError(f"review of game {game_id} is not available for this owner")
        if row.revision != expected_revision:
            raise ReviewRevisionConflictError(
                f"review of game {game_id} is at revision {row.revision}, expected {expected_revision}"
            )
        return row

    def _require_own_finished_game(self, game_id: str, owner_key: str) -> None:
        statement = select(GameRow.status).where(GameRow.id == game_id, GameRow.owner_key == owner_key)
        status = self._session.scalars(statement).one_or_none()
        if status is None:
            raise UnknownReviewGameError(f"game {game_id} is not available for this owner")
        if GameStatus(status) is GameStatus.ACTIVE:
            # A resigned game is over as much as a mated one, and is worth reviewing.
            raise GameNotFinishedError(f"game {game_id} is still active")
