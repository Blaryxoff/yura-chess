"""Reviewing a finished game — against a real MariaDB, never a real engine."""

from __future__ import annotations

import io
from datetime import datetime

import chess
import chess.pgn
import pytest
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import CommandKind, ReviewQuestion, ReviewRequest, route
from yura_chess.application.conversation import ConversationService, ConversationState
from yura_chess.application.game_service import RequestContext
from yura_chess.application.review_service import PLIES_PER_REQUEST, ReviewService
from yura_chess.domain.analysis import MoveCandidate, PositionAnalysis, Score
from yura_chess.domain.game import EngineSettings, GameMode, GameState, GameStatus, PlayerColor
from yura_chess.domain.review import ReviewSection
from yura_chess.engine.stockfish import EngineSearchTimeoutError, EngineUnavailableError
from yura_chess.presentation import pgn
from yura_chess.settings import Settings
from yura_chess.storage.analysis_repository import AnalysisRepository
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.review_repository import ReviewRepository

pytestmark = pytest.mark.anyio

OWNER = "r" * 64

# 1. f3 e5 2. g4 Qh4#: White's two moves are the losses the review must find.
FOOLS_MATE = ("f2f3", "e7e5", "g2g4", "d8h4")


class FakeEngine:
    """Values every move from a fixed table; may fail on demand."""

    def __init__(
        self,
        scores: dict[str, int] | None = None,
        analysis_error: Exception | None = None,
        default: int = 0,
        fail_after: int | None = None,
    ) -> None:
        self.scores = scores or {}
        self.analysis_error = analysis_error
        self.default = default
        self.fail_after = fail_after
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
        if self.fail_after is not None and len(self.analysed) >= self.fail_after:
            raise EngineSearchTimeoutError("slow")
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
    value = f"r{message_id}"
    return RequestContext("shell", "review", value, value.ljust(64, "0"), new)


def finished_game(
    session_factory: sessionmaker[Session],
    moves: tuple[str, ...] = FOOLS_MATE,
    status: GameStatus = GameStatus.FINISHED,
    color: PlayerColor = PlayerColor.WHITE,
) -> GameState:
    """Create the exact finished game a review test needs."""
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        state = repository.create_game(OWNER, color)
        state = repository.append_moves(state.id, OWNER, state.revision, moves, status=status)
        return state


def load(session_factory: sessionmaker[Session], game_id: str) -> GameState:
    with session_scope(session_factory) as session:
        return GameRepository(session).load(game_id, OWNER)


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("разбери партию", ReviewQuestion.SUMMARY),
        ("где был перелом", ReviewQuestion.TURNING_POINT),
        ("какая моя главная ошибка", ReviewQuestion.MAIN_MISTAKE),
        ("сколько ошибок я сделал", ReviewQuestion.MISTAKE_COUNT),
        ("продиктуй партию", ReviewQuestion.MOVES),
        ("покажи pgn", ReviewQuestion.PGN),
        ("сыграть эту позицию заново", ReviewQuestion.REPLAY_POSITION),
        ("продолжить разбор", ReviewQuestion.CONTINUE),
        ("выйти из разбора", ReviewQuestion.EXIT),
    ],
)
def test_review_phrases_are_routed_before_the_game_commands(utterance: str, expected: ReviewQuestion) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.REVIEW
    assert routed.review is not None
    assert routed.review.question is expected


async def test_the_summary_names_the_result_the_counts_and_the_turning_point(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    # Every White move gives up the two pawns the best move was worth.
    engine = FakeEngine(scores={"d2d4": 200}, default=0)
    service = ReviewService(session_factory, engine, offline_settings)
    game = finished_game(session_factory)

    reply = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.SUMMARY))

    assert "Вы проиграли." in reply.text
    assert "грубых ошибок 2" in reply.text
    assert "Перелом" in reply.text
    after = load(session_factory, game.id)
    assert (after.moves, after.revision, after.status) == (game.moves, game.revision, game.status)


async def test_a_clean_game_reports_no_significant_mistakes(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = ReviewService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory)

    reply = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.MISTAKE_COUNT))

    assert "Существенных ошибок" in reply.text


async def test_only_the_player_moves_are_valued(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = ReviewService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory)

    await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.SUMMARY))

    with session_scope(session_factory) as session:
        stored = AnalysisRepository(session).list_for_game(game.id, OWNER)
    assert [point.ply for point in stored] == [0, 2]


async def test_an_existing_checkpoint_is_reused_instead_of_analysed_again(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(scores={"d2d4": 200})
    service = ReviewService(session_factory, engine, offline_settings)
    game = finished_game(session_factory)

    first = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.MISTAKE_COUNT))
    analysed_once = len(engine.analysed)
    second = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.MISTAKE_COUNT))

    assert first.text == second.text
    assert len(engine.analysed) == analysed_once


async def test_a_long_game_is_reviewed_in_bounded_batches_and_can_be_continued(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    # Sixteen quiet knight moves, so the player owns eight plies to value.
    moves = ("g1f3", "g8f6", "f3g1", "f6g8") * 4
    service = ReviewService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory, moves=moves, status=GameStatus.RESIGNED)

    partial = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.MISTAKE_COUNT))
    assert "продолжить разбор" in partial.text
    with session_scope(session_factory) as session:
        assert len(AnalysisRepository(session).list_for_game(game.id, OWNER)) == PLIES_PER_REQUEST

    complete = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.CONTINUE))

    assert "продолжить разбор" not in complete.text
    with session_scope(session_factory) as session:
        assert len(AnalysisRepository(session).list_for_game(game.id, OWNER)) == 8


@pytest.mark.parametrize("error", [EngineUnavailableError("busy"), EngineSearchTimeoutError("slow")])
async def test_a_busy_engine_gives_an_honest_partial_review(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    error: Exception,
) -> None:
    service = ReviewService(session_factory, FakeEngine(analysis_error=error), offline_settings)
    game = finished_game(session_factory)

    reply = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.SUMMARY))

    assert "продолжить разбор" in reply.text
    assert "Вы проиграли." in reply.text
    after = load(session_factory, game.id)
    assert (after.moves, after.revision) == (game.moves, game.revision)


async def test_a_timeout_halfway_keeps_the_moves_it_managed_to_value(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    # One position is valued, the search for the next one never returns.
    engine = FakeEngine(scores={"d2d4": 200}, fail_after=1)
    service = ReviewService(session_factory, engine, offline_settings)
    game = finished_game(session_factory)

    interrupted = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.MISTAKE_COUNT))
    assert "продолжить разбор" in interrupted.text

    engine.fail_after = None
    resumed = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.CONTINUE))

    assert "продолжить разбор" not in resumed.text
    with session_scope(session_factory) as session:
        assert [point.ply for point in AnalysisRepository(session).list_for_game(game.id, OWNER)] == [0, 2]


async def test_the_dictation_pages_through_the_moves_and_stores_its_cursor(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    moves = ("g1f3", "g8f6", "f3g1", "f6g8") * 3
    service = ReviewService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory, moves=moves)

    first = service.dictate(OWNER, game, step=0)
    second = service.dictate(OWNER, game, step=1)

    assert "Ход 1" in first.text
    assert "дальше" in first.text
    assert "Ход 4" in second.text
    with session_scope(session_factory) as session:
        review = ReviewRepository(session).find(game.id, OWNER)
    assert review is not None
    assert (review.section, review.page) == (ReviewSection.MOVES, 1)


async def test_a_stored_cursor_lets_a_new_session_continue_the_dictation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    moves = ("g1f3", "g8f6", "f3g1", "f6g8") * 3
    service = ReviewService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory, moves=moves)
    service.dictate(OWNER, game, step=0)
    service.dictate(OWNER, game, step=1)

    # A fresh service stands for the next Alice session: nothing is carried over.
    resumed = ReviewService(session_factory, FakeEngine(), offline_settings)
    reply = await resumed.answer(OWNER, game, ReviewRequest(ReviewQuestion.CONTINUE))

    assert "Ход 4" in reply.text


async def test_the_pgn_round_trips_into_the_same_final_position(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = ReviewService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory)

    reply = await service.answer(OWNER, game, ReviewRequest(ReviewQuestion.PGN))

    parsed = chess.pgn.read_game(io.StringIO(reply.text))
    assert parsed is not None
    assert parsed.headers["Result"] == "0-1"
    assert parsed.end().board().fen() == game.board().fen()


def test_the_pgn_of_a_game_from_a_custom_position_carries_the_setup_tags() -> None:
    board = chess.Board("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1")
    with_setup = GameState(
        id="branch",
        owner_key=OWNER,
        status=GameStatus.FINISHED,
        player_color=PlayerColor.WHITE,
        initial_fen=board.fen(),
        moves=("e2e4",),
        revision=2,
        engine=EngineSettings(),
        created_at=datetime(2026, 7, 19),
        updated_at=datetime(2026, 7, 19),
    )

    export = pgn.export(with_setup, None)

    parsed = chess.pgn.read_game(io.StringIO(export))
    assert parsed is not None
    assert parsed.headers["FEN"] == board.fen()
    assert parsed.end().board().fen() == with_setup.board().fen()


async def test_the_training_branch_is_a_new_game_and_leaves_the_finished_one_alone(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(scores={"d2d4": 200})
    service = ReviewService(session_factory, engine, offline_settings)
    game = finished_game(session_factory)

    branch_id, speech = await service.start_branch(OWNER, game)

    assert branch_id is not None and branch_id != game.id
    branch = load(session_factory, branch_id)
    assert branch.mode is GameMode.TRAINING
    assert branch.status is GameStatus.ACTIVE
    assert branch.is_player_to_move
    assert "тренера" in speech.text
    after = load(session_factory, game.id)
    assert (after.moves, after.revision, after.status) == (game.moves, game.revision, game.status)


async def test_a_review_question_before_any_finished_game_says_so(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings)

    reply = await conversation.handle(OWNER, "разбери партию", context(1))

    assert "Законченной партии еще нет" in reply.speech.text


async def test_a_review_question_never_touches_a_running_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings)
    finished = finished_game(session_factory)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    running = load(session_factory, started.state.game_id or "")

    reply = await conversation.handle(OWNER, "сколько ошибок я сделал", context(2), started.state)

    assert "порогам" in reply.speech.text or "Существенных ошибок" in reply.speech.text
    after = load(session_factory, running.id)
    assert (after.moves, after.revision, after.pending_engine_turn) == (
        running.moves,
        running.revision,
        running.pending_engine_turn,
    )
    assert load(session_factory, finished.id).revision == finished.revision


async def test_next_turns_the_review_page_while_the_review_is_open(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    moves = ("g1f3", "g8f6", "f3g1", "f6g8") * 3
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory, moves=moves)
    state = ConversationState(game_id=game.id, revision=game.revision)

    dictated = await conversation.handle(OWNER, "продиктуй партию", context(1), state)
    paged = await conversation.handle(OWNER, "дальше", context(2), dictated.state)

    assert dictated.state.reviewing is True
    assert "Ход 4" in paged.speech.text


async def test_leaving_the_review_gives_next_back_to_the_board(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory)
    state = ConversationState(game_id=game.id, revision=game.revision)

    dictated = await conversation.handle(OWNER, "продиктуй партию", context(1), state)
    closed = await conversation.handle(OWNER, "выйти из разбора", context(2), dictated.state)

    assert closed.state.reviewing is False
    with session_scope(session_factory) as session:
        assert ReviewRepository(session).find(game.id, OWNER) is None


async def test_the_training_branch_is_started_only_after_a_confirmation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine(scores={"d2d4": 200})
    conversation = ConversationService(session_factory, engine, offline_settings)
    game = finished_game(session_factory)
    state = ConversationState(game_id=game.id, revision=game.revision)

    asked = await conversation.handle(OWNER, "сыграть эту позицию заново", context(1), state)
    assert "«да»" in asked.speech.text
    assert asked.state.game_id == game.id

    confirmed = await conversation.handle(OWNER, "да", context(2), asked.state)

    assert confirmed.state.game_id != game.id
    assert load(session_factory, confirmed.state.game_id or "").mode is GameMode.TRAINING
    after = load(session_factory, game.id)
    assert (after.moves, after.revision, after.status) == (game.moves, game.revision, game.status)


async def test_declining_the_branch_starts_nothing(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory)
    state = ConversationState(game_id=game.id, revision=game.revision)

    asked = await conversation.handle(OWNER, "сыграть эту позицию заново", context(1), state)
    declined = await conversation.handle(OWNER, "нет", context(2), asked.state)

    assert declined.state.game_id == game.id
    with session_scope(session_factory) as session:
        assert GameRepository(session).find_latest(OWNER) is not None
    assert load(session_factory, game.id).revision == game.revision


async def test_a_review_of_another_owners_game_reads_nothing(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """A forged game id must not open a review or reveal another player's analysis."""
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings)
    game = finished_game(session_factory)
    stranger = "s" * 64

    reply = await conversation.handle(
        stranger,
        "разбери партию",
        context(1),
        ConversationState(game_id=game.id, revision=game.revision),
    )

    assert "Законченной партии еще нет" in reply.speech.text
    with session_scope(session_factory) as session:
        assert ReviewRepository(session).find(game.id, stranger) is None
        assert AnalysisRepository(session).list_for_game(game.id, stranger) == ()
