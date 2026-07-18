"""Lifecycle tests against a real MariaDB with fake engine workers."""

from __future__ import annotations

import asyncio

import chess
import pytest
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.game_service import GameService, RequestContext
from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.domain.results import GameEnd, TurnStatus
from yura_chess.engine.stockfish import EngineSearchTimeoutError, EngineUnavailableError
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import (
    GameNotFoundError,
    GameRepository,
    ReplayFingerprintConflictError,
)

pytestmark = pytest.mark.anyio

SKILL = "skill"
OWNER = "a" * 64
OTHER_OWNER = "b" * 64

# 1. f3 e5 2. g4 Qh4# — the shortest mate, used from either side.
FOOLS_MATE = ("f2f3", "e7e5", "g2g4", "d8h4")
KNIGHT_SHUFFLE = ("g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8")


class FakeEngine:
    """Replays a scripted move list, then falls back to the first legal move."""

    def __init__(self, moves: tuple[str, ...] = (), error: Exception | None = None) -> None:
        self.script = list(moves)
        self.error = error
        self.searches: list[str] = []

    async def best_move(self, board: chess.Board, search_time: float | None = None) -> str:
        if self.error is not None:
            raise self.error
        self.searches.append(board.fen())
        return self.script.pop(0) if self.script else next(iter(board.legal_moves)).uci()


def request(message_id: str, session_id: str = "session", fingerprint: str | None = None) -> RequestContext:
    body = fingerprint or message_id
    return RequestContext(SKILL, session_id, message_id, body.ljust(64, "0")[:64])


def service(session_factory: sessionmaker[Session], engine: FakeEngine) -> GameService:
    return GameService(session_factory, engine)


def load(session_factory: sessionmaker[Session], game_id: str, owner: str = OWNER):  # type: ignore[no-untyped-def]
    with session_scope(session_factory) as session:
        return GameRepository(session).load(game_id, owner)


def seed_game(
    session_factory: sessionmaker[Session],
    owner: str = OWNER,
    player_color: PlayerColor = PlayerColor.WHITE,
    moves: tuple[str, ...] = (),
) -> str:
    """Create a game straight through the repository, bypassing the request path."""
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        state = repository.create_game(owner, player_color)
        if moves:
            repository.append_moves(state.id, owner, state.revision, moves)
        return state.id


async def test_start_as_white_leaves_the_first_move_to_the_player(
    session_factory: sessionmaker[Session],
) -> None:
    engine = FakeEngine()
    result = await service(session_factory, engine).start_game(OWNER, request("m1"))

    assert result.status is TurnStatus.OK
    assert result.moves == ()
    assert result.game_status is GameStatus.ACTIVE
    assert engine.searches == []


async def test_start_as_black_lets_the_engine_open(session_factory: sessionmaker[Session]) -> None:
    engine = FakeEngine(("e2e4",))
    result = await service(session_factory, engine).start_game(OWNER, request("m1"), PlayerColor.BLACK)

    assert result.status is TurnStatus.OK
    assert result.engine_move == "e2e4"
    assert load(session_factory, result.game_id).moves == ("e2e4",)


async def test_player_move_gets_an_engine_reply(session_factory: sessionmaker[Session]) -> None:
    engine = FakeEngine(("e7e5",))
    subject = service(session_factory, engine)
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id

    result = await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    assert (result.player_move, result.engine_move) == ("e2e4", "e7e5")
    state = load(session_factory, game_id)
    assert state.moves == ("e2e4", "e7e5")
    assert state.pending_engine_turn is None


async def test_illegal_move_leaves_the_history_untouched(session_factory: sessionmaker[Session]) -> None:
    engine = FakeEngine()
    subject = service(session_factory, engine)
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id

    result = await subject.play_move(OWNER, game_id, "e2e5", request("m2"))

    assert result.status is TurnStatus.ILLEGAL_MOVE
    assert load(session_factory, game_id).moves == ()
    assert engine.searches == []


async def test_unparsable_move_is_rejected_as_illegal(session_factory: sessionmaker[Session]) -> None:
    subject = service(session_factory, FakeEngine())
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id

    result = await subject.play_move(OWNER, game_id, "ладья", request("m2"))

    assert result.status is TurnStatus.ILLEGAL_MOVE


async def test_checkmate_by_the_player_ends_the_game_without_an_engine_reply(
    session_factory: sessionmaker[Session],
) -> None:
    engine = FakeEngine(FOOLS_MATE[0::2])  # the engine plays White's two losing moves
    subject = service(session_factory, engine)
    game_id = (await subject.start_game(OWNER, request("m1"), PlayerColor.BLACK)).game_id
    await subject.play_move(OWNER, game_id, "e7e5", request("m2"))

    result = await subject.play_move(OWNER, game_id, "d8h4", request("m3"))

    assert result.status is TurnStatus.GAME_OVER
    assert result.outcome is not None
    assert result.outcome.end is GameEnd.CHECKMATE
    assert result.outcome.winner is PlayerColor.BLACK
    assert result.engine_move is None
    state = load(session_factory, game_id)
    assert state.status is GameStatus.FINISHED
    assert state.moves == FOOLS_MATE
    assert state.pending_engine_turn is None


async def test_checkmate_by_the_engine_ends_the_game(session_factory: sessionmaker[Session]) -> None:
    engine = FakeEngine(FOOLS_MATE[1::2])
    subject = service(session_factory, engine)
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    await subject.play_move(OWNER, game_id, "f2f3", request("m2"))

    result = await subject.play_move(OWNER, game_id, "g2g4", request("m3"))

    assert result.status is TurnStatus.GAME_OVER
    assert result.outcome is not None
    assert result.outcome.winner is PlayerColor.BLACK
    assert load(session_factory, game_id).status is GameStatus.FINISHED


async def test_engine_failure_keeps_a_resumable_pending_turn(session_factory: sessionmaker[Session]) -> None:
    engine = FakeEngine(error=EngineUnavailableError("pool saturated"))
    subject = service(session_factory, engine)
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id

    result = await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    assert result.status is TurnStatus.ENGINE_UNAVAILABLE
    assert result.player_move == "e2e4"
    state = load(session_factory, game_id)
    assert state.moves == ("e2e4",)
    assert state.pending_engine_turn is not None
    assert state.pending_engine_turn.player_move_uci == "e2e4"


async def test_retry_of_a_timed_out_request_resumes_without_replaying_the_move(
    session_factory: sessionmaker[Session],
) -> None:
    """The crash-between-A-and-B case: A committed, B never ran."""
    subject = service(session_factory, FakeEngine(error=EngineSearchTimeoutError("no move within 3.0 s")))
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    retry_engine = FakeEngine(("e7e5",))
    result = await service(session_factory, retry_engine).play_move(OWNER, game_id, "e2e4", request("m2"))

    assert result.status is TurnStatus.OK
    assert result.engine_move == "e7e5"
    state = load(session_factory, game_id)
    assert state.moves == ("e2e4", "e7e5")
    assert state.pending_engine_turn is None


async def test_a_new_request_during_a_pending_turn_resumes_instead_of_moving_again(
    session_factory: sessionmaker[Session],
) -> None:
    subject = service(session_factory, FakeEngine(error=EngineUnavailableError("pool saturated")))
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    # The player repeats the move under a new message id after the silence.
    result = await service(session_factory, FakeEngine(("e7e5",))).play_move(OWNER, game_id, "e2e4", request("m3"))

    assert result.player_move == "e2e4"
    assert load(session_factory, game_id).moves == ("e2e4", "e7e5")


async def test_a_turn_settled_mid_search_is_not_applied_twice(session_factory: sessionmaker[Session]) -> None:
    """Transaction B must give way once another writer has bumped the revision."""

    class RacingEngine(FakeEngine):
        """Settles the pending turn from another session while the search runs."""

        def __init__(self, session_factory: sessionmaker[Session], rival_move: str) -> None:
            super().__init__((rival_move,))
            self._session_factory = session_factory
            self._rival_move = rival_move
            self.game_id: str | None = None

        async def best_move(self, board: chess.Board, search_time: float | None = None) -> str:
            assert self.game_id is not None
            with session_scope(self._session_factory) as session:
                repository = GameRepository(session)
                state = repository.load(self.game_id, OWNER)
                pending = state.pending_engine_turn
                assert pending is not None
                repository.finish_engine_turn(self.game_id, OWNER, state.revision, pending.token, self._rival_move)
            return await super().best_move(board, search_time)

    engine = RacingEngine(session_factory, "e7e5")
    subject = service(session_factory, engine)
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    engine.game_id = game_id

    result = await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    assert result.status is TurnStatus.OK
    assert load(session_factory, game_id).moves == ("e2e4", "e7e5")


async def test_exact_replay_returns_the_stored_response(session_factory: sessionmaker[Session]) -> None:
    engine = FakeEngine(("e7e5",))
    subject = service(session_factory, engine)
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    first = await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    second = await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    assert second.replayed is True
    assert (second.moves, second.engine_move) == (first.moves, first.engine_move)
    assert len(engine.searches) == 1
    assert load(session_factory, game_id).moves == ("e2e4", "e7e5")


async def test_another_owner_cannot_play_the_game(session_factory: sessionmaker[Session]) -> None:
    subject = service(session_factory, FakeEngine())
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id

    with pytest.raises(GameNotFoundError):
        await subject.play_move(OTHER_OWNER, game_id, "e2e4", request("m2"))

    assert load(session_factory, game_id).moves == ()


async def test_replay_key_reused_with_another_fingerprint_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    subject = service(session_factory, FakeEngine())
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    with pytest.raises(ReplayFingerprintConflictError):
        await subject.play_move(OWNER, game_id, "d2d4", request("m2", fingerprint="other"))

    assert load(session_factory, game_id).moves[:1] == ("e2e4",)


async def test_resignation_finishes_the_game_for_the_engine(session_factory: sessionmaker[Session]) -> None:
    subject = service(session_factory, FakeEngine())
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id

    result = await subject.resign(OWNER, game_id, request("m2"))

    assert result.status is TurnStatus.GAME_OVER
    assert result.outcome is not None
    assert (result.outcome.end, result.outcome.winner) == (GameEnd.RESIGNATION, PlayerColor.BLACK)
    assert load(session_factory, game_id).status is GameStatus.RESIGNED


async def test_resignation_drops_a_pending_engine_turn(session_factory: sessionmaker[Session]) -> None:
    subject = service(session_factory, FakeEngine(error=EngineUnavailableError("pool saturated")))
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    await subject.resign(OWNER, game_id, request("m3"))

    state = load(session_factory, game_id)
    assert state.status is GameStatus.RESIGNED
    assert state.pending_engine_turn is None


async def test_threefold_repetition_is_a_draw_only_on_demand(session_factory: sessionmaker[Session]) -> None:
    game_id = seed_game(session_factory, moves=KNIGHT_SHUFFLE)
    subject = service(session_factory, FakeEngine())

    result = await subject.claim_draw(OWNER, game_id, request("m1"))

    assert result.status is TurnStatus.GAME_OVER
    assert result.outcome is not None
    assert result.outcome.end is GameEnd.THREEFOLD_REPETITION
    assert load(session_factory, game_id).status is GameStatus.FINISHED


async def test_a_draw_that_cannot_be_claimed_leaves_the_game_running(
    session_factory: sessionmaker[Session],
) -> None:
    game_id = seed_game(session_factory, moves=("e2e4", "e7e5"))
    subject = service(session_factory, FakeEngine())

    result = await subject.claim_draw(OWNER, game_id, request("m1"))

    assert result.status is TurnStatus.DRAW_NOT_CLAIMABLE
    assert load(session_factory, game_id).status is GameStatus.ACTIVE


async def test_undo_takes_back_the_player_move_and_the_engine_reply(
    session_factory: sessionmaker[Session],
) -> None:
    game_id = seed_game(session_factory, moves=("e2e4", "e7e5", "g1f3", "b8c6"))
    subject = service(session_factory, FakeEngine())

    result = await subject.undo_turn(OWNER, game_id, request("m1"))

    assert result.status is TurnStatus.OK
    assert load(session_factory, game_id).moves == ("e2e4", "e7e5")


async def test_undo_for_a_black_player_keeps_the_engine_opening(session_factory: sessionmaker[Session]) -> None:
    game_id = seed_game(session_factory, player_color=PlayerColor.BLACK, moves=("e2e4", "e7e5", "g1f3"))
    subject = service(session_factory, FakeEngine())

    await subject.undo_turn(OWNER, game_id, request("m1"))

    assert load(session_factory, game_id).moves == ("e2e4",)


async def test_undo_is_rejected_while_an_engine_turn_is_pending(session_factory: sessionmaker[Session]) -> None:
    subject = service(session_factory, FakeEngine(error=EngineUnavailableError("pool saturated")))
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    result = await subject.undo_turn(OWNER, game_id, request("m3"))

    assert result.status is TurnStatus.UNDO_REJECTED
    assert load(session_factory, game_id).moves == ("e2e4",)


async def test_undo_with_nothing_of_the_player_to_take_back(session_factory: sessionmaker[Session]) -> None:
    game_id = seed_game(session_factory, player_color=PlayerColor.BLACK, moves=("e2e4",))
    subject = service(session_factory, FakeEngine())

    result = await subject.undo_turn(OWNER, game_id, request("m1"))

    assert result.status is TurnStatus.UNDO_REJECTED
    assert load(session_factory, game_id).moves == ("e2e4",)


async def test_continue_resumes_an_owed_engine_move(session_factory: sessionmaker[Session]) -> None:
    subject = service(session_factory, FakeEngine(error=EngineUnavailableError("pool saturated")))
    game_id = (await subject.start_game(OWNER, request("m1"))).game_id
    await subject.play_move(OWNER, game_id, "e2e4", request("m2"))

    result = await service(session_factory, FakeEngine(("e7e5",))).continue_game(OWNER, game_id, request("m3"))

    assert result.engine_move == "e7e5"
    assert load(session_factory, game_id).moves == ("e2e4", "e7e5")


async def test_continue_only_reports_a_settled_position(session_factory: sessionmaker[Session]) -> None:
    engine = FakeEngine()
    subject = service(session_factory, engine)
    game_id = seed_game(session_factory, moves=("e2e4", "e7e5"))

    result = await subject.continue_game(OWNER, game_id, request("m1"))

    assert result.status is TurnStatus.OK
    assert engine.searches == []


async def test_games_of_two_players_advance_independently(session_factory: sessionmaker[Session]) -> None:
    mine = service(session_factory, FakeEngine(("e7e5",)))
    theirs = service(session_factory, FakeEngine(("c7c5",)))
    my_game = (await mine.start_game(OWNER, request("m1", session_id="s1"))).game_id
    their_game = (await theirs.start_game(OTHER_OWNER, request("m1", session_id="s2"))).game_id

    await asyncio.gather(
        mine.play_move(OWNER, my_game, "e2e4", request("m2", session_id="s1")),
        theirs.play_move(OTHER_OWNER, their_game, "d2d4", request("m2", session_id="s2")),
    )

    assert load(session_factory, my_game, OWNER).moves == ("e2e4", "e7e5")
    assert load(session_factory, their_game, OTHER_OWNER).moves == ("d2d4", "c7c5")
