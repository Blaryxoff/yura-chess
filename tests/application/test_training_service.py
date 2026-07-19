"""Coaching answers, hints and checkpoints — always against a real MariaDB, never a real engine."""

from __future__ import annotations

import chess
import pytest
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import TrainingQuestion, TrainingRequest
from yura_chess.application.conversation import ConversationService, ConversationState
from yura_chess.application.game_service import RequestContext
from yura_chess.application.training_service import TrainingService
from yura_chess.domain.analysis import MoveCandidate, PositionAnalysis, Score
from yura_chess.domain.game import GameMode, PlayerColor
from yura_chess.engine.stockfish import EngineSearchTimeoutError, EngineUnavailableError
from yura_chess.settings import Settings
from yura_chess.storage.analysis_repository import AnalysisRepository
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository

pytestmark = pytest.mark.anyio

OWNER = "t" * 64


class FakeEngine:
    """Plays the first legal move and values positions from a fixed score table."""

    def __init__(
        self,
        scores: dict[str, int] | None = None,
        analysis_error: Exception | None = None,
        default: int = 0,
    ) -> None:
        self.scores = scores or {}
        self.analysis_error = analysis_error
        self.default = default
        self.analysed: list[str] = []

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        return next(iter(board.legal_moves)).uci()

    async def analyse(
        self,
        board: chess.Board,
        search_time: float | None = None,
        candidates: int | None = None,
    ) -> PositionAnalysis:
        if self.analysis_error is not None:
            raise self.analysis_error
        self.analysed.append(board.fen())
        lines = [
            MoveCandidate(
                move=move.uci(),
                score=Score(centipawns=self.scores.get(move.uci(), self.default)),
                principal_variation=(move.uci(),),
            )
            for move in board.legal_moves
        ]
        lines.sort(key=lambda candidate: candidate.score.as_centipawns(), reverse=True)
        return PositionAnalysis(
            fen=board.fen(),
            side_to_move=PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK,
            depth=12,
            candidates=tuple(lines[: candidates or 3]),
        )


def context(message_id: int, *, new: bool = False) -> RequestContext:
    value = f"t{message_id}"
    return RequestContext("shell", "training", value, value.ljust(64, "0"), new)


def start_training_game(session_factory: sessionmaker[Session], moves: tuple[str, ...] = ()) -> str:
    """Create a training game directly, so the tests choose the exact position."""
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        state = repository.create_game(OWNER, PlayerColor.WHITE, mode=GameMode.TRAINING)
        if moves:
            state = repository.append_moves(state.id, OWNER, state.revision, moves)
        return state.id


def load(session_factory: sessionmaker[Session], game_id: str):  # noqa: ANN201 - GameState
    with session_scope(session_factory) as session:
        return GameRepository(session).load(game_id, OWNER)


async def test_trainer_is_switched_on_only_by_an_explicit_command(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    assert load(session_factory, started.state.game_id or "").mode is GameMode.GAME

    reply = await conversation.handle(OWNER, "включи режим тренера", context(2), started.state)

    assert "тренера" in reply.speech.text
    assert load(session_factory, started.state.game_id or "").mode is GameMode.TRAINING


async def test_advice_in_an_honest_game_offers_the_trainer_without_answering(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(scores={"e2e4": 120})
    conversation = ConversationService(session_factory, engine, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    reply = await conversation.handle(OWNER, "дай подсказку", context(2), started.state)

    assert "включи режим тренера" in reply.speech.text
    assert "e2e4" not in reply.speech.text
    assert engine.analysed == []


async def test_evaluation_is_verbal_first_and_numeric_on_request(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(default=180)
    service = TrainingService(session_factory, engine, offline_settings)
    game = load(session_factory, start_training_game(session_factory))

    verbal = await service.answer(OWNER, game, TrainingRequest(TrainingQuestion.EVALUATION), context(1))
    numeric = await service.answer(OWNER, game, TrainingRequest(TrainingQuestion.EVALUATION_NUMBER), context(2))

    assert "заметный перевес" in verbal.text
    assert "1.8" in numeric.text


async def test_candidates_name_at_most_three_moves_and_change_nothing(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = TrainingService(session_factory, FakeEngine(), offline_settings)
    game_id = start_training_game(session_factory)
    game = load(session_factory, game_id)

    reply = await service.answer(OWNER, game, TrainingRequest(TrainingQuestion.CANDIDATES), context(1))

    listed = reply.text.split(":", 1)[1]
    assert listed.count(",") == 2
    after = load(session_factory, game_id)
    assert (after.moves, after.revision, after.pending_engine_turn) == (game.moves, game.revision, None)


async def test_preview_analyses_a_suggested_move_without_playing_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = TrainingService(session_factory, FakeEngine(default=-40), offline_settings)
    game_id = start_training_game(session_factory)
    game = load(session_factory, game_id)

    reply = await service.answer(
        OWNER,
        game,
        TrainingRequest(TrainingQuestion.PREVIEW, move_text="конь эф три"),
        context(1),
    )

    assert "Ход я не делаю" in reply.text
    assert load(session_factory, game_id).moves == ()


async def test_preview_of_an_impossible_move_uses_the_existing_explainer(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = TrainingService(session_factory, FakeEngine(), offline_settings)
    game = load(session_factory, start_training_game(session_factory))

    reply = await service.answer(
        OWNER,
        game,
        TrainingRequest(TrainingQuestion.PREVIEW, move_text="пешка е два е пять"),
        context(1),
    )

    assert "Ход я не делаю" not in reply.text
    assert reply.text


async def test_the_engine_move_is_explained_with_one_concrete_purpose(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = TrainingService(session_factory, FakeEngine(), offline_settings)
    # 1. e4 e5 2. Bc4 Nc6 3. Qh5, and Black is threatened with mate on f7.
    game_id = start_training_game(session_factory, ("e2e4", "e7e5", "b1c3", "b8c6"))
    game = load(session_factory, game_id)

    reply = await service.answer(OWNER, game, TrainingRequest(TrainingQuestion.WHY_MOVE), context(1))

    assert reply.text.startswith("Мой ход")
    assert load(session_factory, game_id).revision == game.revision


async def test_a_threat_is_named_only_when_the_free_move_would_win_material(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    quiet = TrainingService(session_factory, FakeEngine(), offline_settings)
    game = load(session_factory, start_training_game(session_factory, ("e2e4", "e7e5")))

    reply = await quiet.answer(OWNER, game, TrainingRequest(TrainingQuestion.THREAT), context(1))

    assert "Ясной угрозы сейчас нет." == reply.text


async def test_hint_climbs_one_stage_per_request_and_repeats_on_replay(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = TrainingService(session_factory, FakeEngine(scores={"e2e4": 90}), offline_settings)
    game_id = start_training_game(session_factory)
    hint = TrainingRequest(TrainingQuestion.HINT)

    texts = []
    for message_id in range(1, 6):
        game = load(session_factory, game_id)
        texts.append((await service.answer(OWNER, game, hint, context(message_id))).text)
    replayed = await service.answer(OWNER, load(session_factory, game_id), hint, context(5))

    assert load(session_factory, game_id).hint_stage == 4
    assert "пешка" in texts[1]
    assert "поле назначения — e4" in texts[2]
    assert "e2 e4" in texts[3]
    assert replayed.text == texts[4]


async def test_a_training_move_is_valued_before_the_engine_answers(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    # The best move is worth far more than the one the player will make.
    engine = FakeEngine(scores={"d2d4": 200, "a2a3": -100})
    conversation = ConversationService(session_factory, engine, offline_settings)
    game_id = start_training_game(session_factory)
    state = ConversationState(game_id=game_id)

    await conversation.handle(OWNER, "пешка а два а три", context(1), state)

    with session_scope(session_factory) as session:
        checkpoint = AnalysisRepository(session).find(game_id, OWNER, 0)
    assert checkpoint is not None
    assert checkpoint.centipawn_loss == 200


async def test_where_i_went_wrong_reports_the_last_significant_loss(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(scores={"d2d4": 200, "a2a3": -100})
    conversation = ConversationService(session_factory, engine, offline_settings)
    game_id = start_training_game(session_factory)

    moved = await conversation.handle(OWNER, "пешка а два а три", context(1), ConversationState(game_id=game_id))
    asked = await conversation.handle(OWNER, "где я ошибся", context(2), moved.state)

    assert "Внимание" in moved.speech.text
    assert "a2" in asked.speech.text
    assert "2.0 пешки" in asked.speech.text


async def test_keeping_the_move_leaves_the_game_alone_and_taking_it_back_undoes_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(scores={"d2d4": 200, "a2a3": -100})
    conversation = ConversationService(session_factory, engine, offline_settings)
    game_id = start_training_game(session_factory)

    moved = await conversation.handle(OWNER, "пешка а два а три", context(1), ConversationState(game_id=game_id))
    kept = await conversation.handle(OWNER, "оставить мой ход", context(2), moved.state)
    assert load(session_factory, game_id).moves[0] == "a2a3"

    await conversation.handle(OWNER, "вернуть ход", context(3), kept.state)
    assert load(session_factory, game_id).moves == ()


async def test_a_taken_back_move_is_no_longer_reported_as_a_mistake(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(scores={"d2d4": 200, "a2a3": -100})
    conversation = ConversationService(session_factory, engine, offline_settings)
    service = TrainingService(session_factory, engine, offline_settings)
    game_id = start_training_game(session_factory)

    moved = await conversation.handle(OWNER, "пешка а два а три", context(1), ConversationState(game_id=game_id))
    await conversation.handle(OWNER, "вернуть ход", context(2), moved.state)

    assert service.last_mistake(OWNER, load(session_factory, game_id)) is None


@pytest.mark.parametrize("error", [EngineUnavailableError("busy"), EngineSearchTimeoutError("slow")])
async def test_a_busy_engine_answers_honestly_and_leaves_the_game_untouched(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    error: Exception,
) -> None:
    service = TrainingService(session_factory, FakeEngine(analysis_error=error), offline_settings)
    game_id = start_training_game(session_factory)
    game = load(session_factory, game_id)

    reply = await service.answer(OWNER, game, TrainingRequest(TrainingQuestion.EVALUATION), context(1))

    assert "Партия не изменилась" in reply.text
    after = load(session_factory, game_id)
    assert (after.moves, after.revision, after.hint_stage) == (game.moves, game.revision, game.hint_stage)


async def test_a_move_still_plays_when_the_analysis_fails(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(analysis_error=EngineUnavailableError("busy"))
    conversation = ConversationService(session_factory, engine, offline_settings)
    game_id = start_training_game(session_factory)

    reply = await conversation.handle(OWNER, "пешка е два е четыре", context(1), ConversationState(game_id=game_id))

    assert reply.turn is not None
    assert load(session_factory, game_id).moves[0] == "e2e4"
    with session_scope(session_factory) as session:
        assert AnalysisRepository(session).find(game_id, OWNER, 0) is None
