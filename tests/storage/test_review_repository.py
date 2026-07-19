"""Review cursors: what may be reviewed, how the cursor moves and where it stops."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.domain.review import ReviewSection
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import GameReviewRow, GameRow
from yura_chess.storage.review_repository import (
    GameNotFinishedError,
    InvalidReviewCursorError,
    ReviewRepository,
    ReviewRevisionConflictError,
    UnknownReviewGameError,
)

OWNER = "a" * 64
OTHER_OWNER = "b" * 64


@pytest.fixture
def reviews(session: Session) -> ReviewRepository:
    return ReviewRepository(session)


def finished_game(repository: GameRepository, owner_key: str = OWNER, status: GameStatus = GameStatus.FINISHED) -> str:
    game = repository.create_game(owner_key, PlayerColor.WHITE)
    repository.append_moves(game.id, owner_key, game.revision, ("e2e4", "e7e5"), status=status)
    return game.id


def test_a_started_review_begins_at_the_summary(reviews: ReviewRepository, repository: GameRepository) -> None:
    review = reviews.start(finished_game(repository), OWNER)

    assert review.section is ReviewSection.SUMMARY
    assert (review.ply, review.page, review.revision) == (0, 0, 1)


def test_a_resigned_game_is_reviewable(reviews: ReviewRepository, repository: GameRepository) -> None:
    game_id = finished_game(repository, status=GameStatus.RESIGNED)

    assert reviews.start(game_id, OWNER).game_id == game_id


def test_an_active_game_cannot_be_reviewed(
    session: Session,
    reviews: ReviewRepository,
    repository: GameRepository,
) -> None:
    game = repository.create_game(OWNER, PlayerColor.WHITE)

    with pytest.raises(GameNotFinishedError):
        reviews.start(game.id, OWNER)

    assert session.scalars(select(GameReviewRow)).all() == []


def test_a_foreign_game_cannot_be_reviewed(
    session: Session,
    reviews: ReviewRepository,
    repository: GameRepository,
) -> None:
    game_id = finished_game(repository)

    with pytest.raises(UnknownReviewGameError):
        reviews.start(game_id, OTHER_OWNER)

    assert session.scalars(select(GameReviewRow)).all() == []


def test_starting_twice_resumes_one_review(
    session: Session,
    reviews: ReviewRepository,
    repository: GameRepository,
) -> None:
    game_id = finished_game(repository)
    started = reviews.start(game_id, OWNER)
    reviews.set_cursor(game_id, OWNER, started.revision, ReviewSection.MOVES, ply=6, page=2)

    resumed = reviews.start(game_id, OWNER)

    assert (resumed.section, resumed.ply, resumed.page) == (ReviewSection.MOVES, 6, 2)
    assert len(session.scalars(select(GameReviewRow)).all()) == 1


def test_a_never_started_review_is_not_an_error(reviews: ReviewRepository, repository: GameRepository) -> None:
    assert reviews.find(finished_game(repository), OWNER) is None
    assert reviews.find_latest(OWNER) is None


def test_the_cursor_is_absolute(reviews: ReviewRepository, repository: GameRepository) -> None:
    game_id = finished_game(repository)
    review = reviews.start(game_id, OWNER)

    moved = reviews.set_cursor(game_id, OWNER, review.revision, ReviewSection.MISTAKES, ply=3, page=1)
    again = reviews.set_cursor(game_id, OWNER, moved.revision, ReviewSection.MISTAKES, ply=3, page=1)

    assert (again.section, again.ply, again.page) == (ReviewSection.MISTAKES, 3, 1)
    assert again.revision == review.revision + 2


@pytest.mark.parametrize(("ply", "page"), [(-1, 0), (0, -1)])
def test_a_negative_cursor_is_rejected(
    reviews: ReviewRepository,
    repository: GameRepository,
    ply: int,
    page: int,
) -> None:
    game_id = finished_game(repository)
    review = reviews.start(game_id, OWNER)

    with pytest.raises(InvalidReviewCursorError):
        reviews.set_cursor(game_id, OWNER, review.revision, ReviewSection.MOVES, ply=ply, page=page)

    assert reviews.find(game_id, OWNER) == review


def test_a_stale_revision_is_rejected(reviews: ReviewRepository, repository: GameRepository) -> None:
    game_id = finished_game(repository)
    review = reviews.start(game_id, OWNER)
    reviews.set_cursor(game_id, OWNER, review.revision, ReviewSection.TURNING_POINT)

    with pytest.raises(ReviewRevisionConflictError):
        reviews.set_cursor(game_id, OWNER, review.revision, ReviewSection.MOVES)


def test_a_foreign_cursor_cannot_be_moved(reviews: ReviewRepository, repository: GameRepository) -> None:
    game_id = finished_game(repository)
    review = reviews.start(game_id, OWNER)

    with pytest.raises(UnknownReviewGameError):
        reviews.set_cursor(game_id, OTHER_OWNER, review.revision, ReviewSection.MOVES)


def test_one_owner_cannot_read_anothers_review(reviews: ReviewRepository, repository: GameRepository) -> None:
    game_id = finished_game(repository)
    reviews.start(game_id, OWNER)

    assert reviews.find(game_id, OTHER_OWNER) is None
    assert reviews.find_latest(OTHER_OWNER) is None


def test_a_review_survives_a_new_session(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as writing:
        game_id = finished_game(GameRepository(writing))
        started = ReviewRepository(writing).start(game_id, OWNER)
        ReviewRepository(writing).set_cursor(game_id, OWNER, started.revision, ReviewSection.MOVES, ply=4, page=1)
        writing.commit()

    with session_factory() as reading:
        resumed = ReviewRepository(reading).find_latest(OWNER)

    assert resumed is not None
    assert (resumed.game_id, resumed.section, resumed.ply, resumed.page) == (game_id, ReviewSection.MOVES, 4, 1)


def test_finishing_a_review_removes_it(session: Session, reviews: ReviewRepository, repository: GameRepository) -> None:
    game_id = finished_game(repository)
    reviews.start(game_id, OWNER)

    assert reviews.finish(game_id, OWNER) is True
    assert reviews.finish(game_id, OWNER) is False
    assert session.scalars(select(GameReviewRow)).all() == []


def test_a_foreign_owner_cannot_finish_a_review(
    session: Session,
    reviews: ReviewRepository,
    repository: GameRepository,
) -> None:
    game_id = finished_game(repository)
    reviews.start(game_id, OWNER)

    assert reviews.finish(game_id, OTHER_OWNER) is False
    assert len(session.scalars(select(GameReviewRow)).all()) == 1


def test_deleting_the_game_removes_its_review(
    session: Session,
    reviews: ReviewRepository,
    repository: GameRepository,
) -> None:
    game_id = finished_game(repository)
    reviews.start(game_id, OWNER)

    session.delete(session.get(GameRow, game_id))
    session.flush()

    assert session.scalars(select(GameReviewRow)).all() == []


def test_a_concurrent_start_is_taken_over(
    session: Session,
    reviews: ReviewRepository,
    repository: GameRepository,
) -> None:
    """An earlier request already opened this review: the second one resumes it."""
    game_id = finished_game(repository)
    session.add(
        GameReviewRow(
            game_id=game_id,
            owner_key=OWNER,
            section=ReviewSection.MISTAKES.value,
            ply=5,
            page=1,
            revision=3,
        )
    )
    session.flush()

    started = reviews.start(game_id, OWNER)

    assert (started.section, started.ply, started.revision) == (ReviewSection.MISTAKES, 5, 3)
    assert len(session.scalars(select(GameReviewRow)).all()) == 1
