"""Analysis checkpoints: their thresholds, their idempotency and their owner boundary."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.domain.analysis import (
    BLUNDER_CENTIPAWNS,
    INACCURACY_CENTIPAWNS,
    MISTAKE_CENTIPAWNS,
    AnalysisCheckpoint,
    AnalysisEngineSettings,
    MoveQuality,
    Score,
    centipawn_loss,
    classify_loss,
    position_hash,
)
from yura_chess.domain.game import START_FEN, PlayerColor
from yura_chess.storage.analysis_repository import AnalysisRepository, UnknownCheckpointGameError
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import AnalysisCheckpointRow, GameRow

OWNER = "a" * 64
OTHER_OWNER = "b" * 64

ENGINE = AnalysisEngineSettings(depth=12, search_time_ms=500, skill_level=5)
SLIGHTLY_BETTER = Score(centipawns=20)
SLIGHTLY_WORSE = Score(centipawns=-30)


@pytest.fixture
def checkpoints(session: Session) -> AnalysisRepository:
    return AnalysisRepository(session)


@pytest.fixture
def game_id(repository: GameRepository) -> str:
    return repository.create_game(OWNER, PlayerColor.WHITE).id


def make_checkpoint(
    game_id: str,
    ply: int = 0,
    owner_key: str = OWNER,
    score_before: Score = SLIGHTLY_BETTER,
    score_after: Score = SLIGHTLY_WORSE,
) -> AnalysisCheckpoint:
    return AnalysisCheckpoint(
        game_id=game_id,
        owner_key=owner_key,
        ply=ply,
        position_hash=position_hash(START_FEN),
        score_before=score_before,
        score_after=score_after,
        centipawn_loss=centipawn_loss(score_before, score_after),
        engine=ENGINE,
    )


def test_documented_thresholds_are_the_shared_constants() -> None:
    assert (INACCURACY_CENTIPAWNS, MISTAKE_CENTIPAWNS, BLUNDER_CENTIPAWNS) == (50, 100, 200)


@pytest.mark.parametrize(
    ("loss", "quality"),
    [
        (-500, MoveQuality.GOOD),
        (0, MoveQuality.GOOD),
        (49, MoveQuality.GOOD),
        (50, MoveQuality.INACCURACY),
        (99, MoveQuality.INACCURACY),
        (100, MoveQuality.MISTAKE),
        (199, MoveQuality.MISTAKE),
        (200, MoveQuality.BLUNDER),
    ],
)
def test_a_loss_is_named_by_its_threshold(loss: int, quality: MoveQuality) -> None:
    assert classify_loss(loss) is quality


def test_allowing_a_forced_mate_is_a_blunder() -> None:
    loss = centipawn_loss(Score(centipawns=30), Score(mate_in=-3))

    assert classify_loss(loss) is MoveQuality.BLUNDER


def test_losing_a_forced_mate_is_a_blunder() -> None:
    loss = centipawn_loss(Score(mate_in=2), Score(centipawns=40))

    assert classify_loss(loss) is MoveQuality.BLUNDER


def test_a_postponed_mate_is_still_a_good_move() -> None:
    loss = centipawn_loss(Score(mate_in=3), Score(mate_in=8))

    assert classify_loss(loss) is MoveQuality.GOOD


def test_stored_checkpoint_reports_its_quality(checkpoints: AnalysisRepository, game_id: str) -> None:
    stored = checkpoints.upsert(make_checkpoint(game_id, score_after=Score(centipawns=-200)))

    assert stored.centipawn_loss == 220
    assert stored.quality is MoveQuality.BLUNDER


def test_a_never_valued_move_is_not_an_error(checkpoints: AnalysisRepository, game_id: str) -> None:
    assert checkpoints.find(game_id, OWNER, ply=4) is None
    assert checkpoints.list_for_game(game_id, OWNER) == ()


def test_repeated_upsert_keeps_one_row(session: Session, checkpoints: AnalysisRepository, game_id: str) -> None:
    checkpoint = make_checkpoint(game_id, ply=2)

    first = checkpoints.upsert(checkpoint)
    second = checkpoints.upsert(checkpoint)

    assert first == checkpoint
    assert second == checkpoint
    assert len(session.scalars(select(AnalysisCheckpointRow)).all()) == 1


def test_upsert_replaces_an_earlier_verdict(session: Session, checkpoints: AnalysisRepository, game_id: str) -> None:
    checkpoints.upsert(make_checkpoint(game_id, ply=2))

    revalued = checkpoints.upsert(
        make_checkpoint(game_id, ply=2, score_before=Score(mate_in=4), score_after=Score(mate_in=3)),
    )

    assert revalued.score_before == Score(mate_in=4)
    assert revalued.quality is MoveQuality.GOOD
    assert len(session.scalars(select(AnalysisCheckpointRow)).all()) == 1


def test_checkpoints_are_listed_by_ply(checkpoints: AnalysisRepository, game_id: str) -> None:
    for ply in (4, 0, 2):
        checkpoints.upsert(make_checkpoint(game_id, ply=ply))

    assert [checkpoint.ply for checkpoint in checkpoints.list_for_game(game_id, OWNER)] == [0, 2, 4]


def test_checkpoints_survive_a_new_session(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as writing:
        stored_game = GameRepository(writing).create_game(OWNER, PlayerColor.WHITE)
        AnalysisRepository(writing).upsert(make_checkpoint(stored_game.id, ply=6))
        writing.commit()

    with session_factory() as reading:
        reloaded = AnalysisRepository(reading).find(stored_game.id, OWNER, ply=6)

    assert reloaded is not None
    assert reloaded.engine == ENGINE


def test_deleting_the_game_removes_its_checkpoints(
    session: Session,
    checkpoints: AnalysisRepository,
    game_id: str,
) -> None:
    checkpoints.upsert(make_checkpoint(game_id))

    session.delete(session.get(GameRow, game_id))
    session.flush()

    assert session.scalars(select(AnalysisCheckpointRow)).all() == []


def test_one_owner_cannot_read_anothers_checkpoints(checkpoints: AnalysisRepository, game_id: str) -> None:
    checkpoints.upsert(make_checkpoint(game_id))

    assert checkpoints.find(game_id, OTHER_OWNER, ply=0) is None
    assert checkpoints.list_for_game(game_id, OTHER_OWNER) == ()


def test_a_foreign_game_cannot_be_valued(session: Session, checkpoints: AnalysisRepository, game_id: str) -> None:
    with pytest.raises(UnknownCheckpointGameError):
        checkpoints.upsert(make_checkpoint(game_id, owner_key=OTHER_OWNER))

    assert session.scalars(select(AnalysisCheckpointRow)).all() == []


def test_a_concurrent_insert_is_taken_over(session: Session, checkpoints: AnalysisRepository, game_id: str) -> None:
    """An earlier request already valued this move: the second one overwrites it."""
    session.add(
        AnalysisCheckpointRow(
            game_id=game_id,
            ply=1,
            owner_key=OWNER,
            position_hash=position_hash(START_FEN),
            score_before_centipawns=0,
            score_after_centipawns=0,
            centipawn_loss=0,
            engine_depth=1,
            engine_search_time_ms=1,
            engine_skill_level=1,
        )
    )
    session.flush()

    stored = checkpoints.upsert(make_checkpoint(game_id, ply=1))

    assert stored.engine == ENGINE
    assert len(session.scalars(select(AnalysisCheckpointRow)).all()) == 1


def test_taking_a_move_back_drops_the_checkpoints_that_valued_it(
    session: Session,
    checkpoints: AnalysisRepository,
    repository: GameRepository,
) -> None:
    """A verdict belongs to one concrete move, never to the ply it stood on."""
    game = repository.create_game(OWNER, PlayerColor.WHITE)
    played = repository.append_moves(game.id, OWNER, expected_revision=game.revision, moves=("e2e4", "e7e5"))
    checkpoints.upsert(make_checkpoint(game.id, ply=0))
    checkpoints.upsert(make_checkpoint(game.id, ply=1))
    session.flush()

    repository.truncate_moves(game.id, OWNER, expected_revision=played.revision, keep_plies=1)

    assert [point.ply for point in checkpoints.list_for_game(game.id, OWNER)] == [0]
