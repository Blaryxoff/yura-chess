"""Whole games end to end: twenty golden games, parallel users and a saturated pool.

These exercise `GameService` against a real MariaDB, which is the layer that owns
the two-transaction turn. The engine is a deterministic fake — the real binary
belongs to the deployment smoke check, not to the suite. Every game is replayed
from its own UCI history and compared with the board the test kept in parallel,
so a divergence between storage and the rules fails here rather than on Firebat.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

import chess
import pytest
from settings_fixtures import TEST_IDENTITY_SALT, UNREACHABLE_DATABASE_URL
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.game_service import GameService, RequestContext
from yura_chess.domain.game import GameStatus
from yura_chess.domain.results import GameEnd, TurnStatus, automatic_outcome
from yura_chess.engine.stockfish import EngineProcess, EngineUnavailableError, StockfishPool
from yura_chess.settings import Settings

pytestmark = pytest.mark.anyio

GOLDEN_GAMES = 20
# One turn is the player's move plus the engine's reply. The longest golden game
# takes 143 of them; the cap only stops a runaway.
MAX_TURNS = 200

# The ending each seed reaches, recorded from a known-good run. Pinning them is
# the point of a golden test: a regression in ending detection, in move
# application or in history replay changes an entry here instead of passing
# silently as "the game just did not finish".
GOLDEN_ENDINGS: dict[int, GameEnd] = {
    0: GameEnd.FIVEFOLD_REPETITION,
    1: GameEnd.INSUFFICIENT_MATERIAL,
    2: GameEnd.SEVENTY_FIVE_MOVES,
    3: GameEnd.INSUFFICIENT_MATERIAL,
    4: GameEnd.INSUFFICIENT_MATERIAL,
    5: GameEnd.INSUFFICIENT_MATERIAL,
    6: GameEnd.INSUFFICIENT_MATERIAL,
    7: GameEnd.INSUFFICIENT_MATERIAL,
    8: GameEnd.INSUFFICIENT_MATERIAL,
    9: GameEnd.INSUFFICIENT_MATERIAL,
    10: GameEnd.INSUFFICIENT_MATERIAL,
    11: GameEnd.INSUFFICIENT_MATERIAL,
    12: GameEnd.INSUFFICIENT_MATERIAL,
    13: GameEnd.INSUFFICIENT_MATERIAL,
    14: GameEnd.SEVENTY_FIVE_MOVES,
    15: GameEnd.CHECKMATE,
    16: GameEnd.INSUFFICIENT_MATERIAL,
    17: GameEnd.INSUFFICIENT_MATERIAL,
    18: GameEnd.INSUFFICIENT_MATERIAL,
    19: GameEnd.INSUFFICIENT_MATERIAL,
}


def pick_move(board: chess.Board, seed: int) -> chess.Move:
    """Deterministic legal move: same seed and position always give the same choice.

    Mate is taken when offered, then captures and checks, so the games reach real
    endings instead of shuffling until the ply cap.
    """
    moves = sorted(board.legal_moves, key=lambda move: move.uci())
    for move in moves:
        board.push(move)
        mate = board.is_checkmate()
        board.pop()
        if mate:
            return move
    forcing = [move for move in moves if board.is_capture(move) or board.gives_check(move)]
    candidates = forcing or moves
    return candidates[(seed * 2654435761 + len(board.move_stack)) % len(candidates)]


class ScriptedEngine:
    """Answers like the pool would, without a process."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.searches = 0

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        self.searches += 1
        return pick_move(board, self.seed).uci()


@dataclass
class GameDriver:
    """Plays one game and keeps its own board to compare the stored history against."""

    service: GameService
    owner: str
    game_id: str
    seed: int
    board: chess.Board

    async def play(self) -> tuple[TurnStatus, int]:
        plies = 0
        for step in range(MAX_TURNS):
            if automatic_outcome(self.board) is not None:
                break
            move = pick_move(self.board, self.seed + 1)
            result = await self.service.play_move(
                self.owner,
                self.game_id,
                move.uci(),
                context(self.owner, step),
            )
            assert result.status in {TurnStatus.OK, TurnStatus.GAME_OVER}, result
            self.board.push(move)
            plies += 1
            if result.engine_move is not None:
                self.board.push(chess.Move.from_uci(result.engine_move))
                plies += 1
            assert result.moves == tuple(move.uci() for move in self.board.move_stack)
            assert result.fen == self.board.fen()
            if result.status is TurnStatus.GAME_OVER:
                return TurnStatus.GAME_OVER, plies
        return TurnStatus.OK, plies


def golden_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": UNREACHABLE_DATABASE_URL,
        "identity_salt": TEST_IDENTITY_SALT,
        "engine_pool_size": 2,
        "engine_acquire_timeout_seconds": 0.1,
        "engine_move_deadline_seconds": 0.2,
        "engine_move_time_seconds": 0.05,
        "engine_restart_delay_seconds": 0.01,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def context(owner: str, step: int, session: str = "s") -> RequestContext:
    return RequestContext(
        skill_id="golden",
        session_id=f"{session}-{owner}",
        message_id=str(step),
        fingerprint=f"{owner}-{step}"[:64],
    )


async def open_game(service: GameService, owner: str, seed: int) -> GameDriver:
    start = await service.start_game(owner, context(owner, -1))
    return GameDriver(service=service, owner=owner, game_id=start.game_id, seed=seed, board=chess.Board())


@pytest.mark.parametrize("seed", range(GOLDEN_GAMES))
async def test_a_golden_game_stays_consistent_with_its_move_history(
    session_factory: sessionmaker[Session],
    seed: int,
) -> None:
    service = GameService(session_factory, ScriptedEngine(seed))
    driver = await open_game(service, f"owner-golden-{seed}", seed)

    status, plies = await driver.play()

    assert plies > 0
    # Whatever the ending, the position the service reports is the position the
    # rules produce from the stored moves alone.
    final = await service.continue_game(driver.owner, driver.game_id, context(driver.owner, 9000))
    assert final.fen == driver.board.fen()
    assert final.moves == tuple(move.uci() for move in driver.board.move_stack)

    outcome = automatic_outcome(driver.board)
    assert outcome is not None
    assert outcome.end is GOLDEN_ENDINGS[seed]
    assert status is TurnStatus.GAME_OVER
    # The service must have closed the game itself, not just stopped answering.
    assert final.game_status is GameStatus.FINISHED


def test_the_golden_games_cover_more_than_one_ending() -> None:
    """A suite where every game ended the same way would barely test ending detection."""
    assert len(GOLDEN_ENDINGS) == GOLDEN_GAMES
    assert len(set(GOLDEN_ENDINGS.values())) >= 3
    # The endings a real game reaches without a claim, each proven by some seed.
    assert {GameEnd.CHECKMATE, GameEnd.SEVENTY_FIVE_MOVES, GameEnd.FIVEFOLD_REPETITION} <= set(GOLDEN_ENDINGS.values())


async def test_a_replayed_request_never_moves_a_golden_game_twice(
    session_factory: sessionmaker[Session],
) -> None:
    owner = "owner-replay"
    service = GameService(session_factory, ScriptedEngine(7))
    driver = await open_game(service, owner, 7)

    first = await service.play_move(owner, driver.game_id, "e2e4", context(owner, 0))
    repeat = await service.play_move(owner, driver.game_id, "e2e4", context(owner, 0))

    assert repeat.replayed is True
    assert repeat.moves == first.moves
    assert repeat.revision == first.revision
    assert first.moves[0] == "e2e4"
    assert len(first.moves) == 2


async def test_parallel_users_play_independent_games(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    users = [f"owner-parallel-{index}" for index in range(6)]
    service = GameService(session_factory, ScriptedEngine(3))
    drivers = [await open_game(service, owner, index) for index, owner in enumerate(users)]

    async def few_moves(driver: GameDriver) -> None:
        for step in range(4):
            move = pick_move(driver.board, driver.seed + 1)
            result = await driver.service.play_move(
                driver.owner, driver.game_id, move.uci(), context(driver.owner, step)
            )
            driver.board.push(move)
            if result.engine_move is not None:
                driver.board.push(chess.Move.from_uci(result.engine_move))

    await asyncio.gather(*(few_moves(driver) for driver in drivers))

    with database_engine.begin() as connection:
        rows = connection.execute(text("SELECT game_id, COUNT(*) FROM game_moves GROUP BY game_id")).all()
    assert len(rows) == len(users)
    # No game absorbed another user's moves.
    for driver in drivers:
        state = await service.continue_game(driver.owner, driver.game_id, context(driver.owner, 500))
        assert state.fen == driver.board.fen()
        assert len(state.moves) == len(driver.board.move_stack)


class BlockingProcess:
    """Holds its worker until the test releases it."""

    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def best_move(self, board: chess.Board, search_time: float) -> str:
        self.release.wait(timeout=5.0)
        return next(iter(board.legal_moves)).uci()

    def close(self) -> None:
        return None


async def test_a_saturated_pool_answers_without_losing_the_player_move(
    session_factory: sessionmaker[Session],
) -> None:
    release = threading.Event()
    settings = golden_settings()
    pool = StockfishPool(settings, process_factory=lambda: _blocking(release))
    await pool.start()
    service = GameService(session_factory, pool)

    owners = [f"owner-saturated-{index}" for index in range(5)]
    drivers = [await open_game(service, owner, index) for index, owner in enumerate(owners)]

    try:
        results = await asyncio.gather(
            *(service.play_move(driver.owner, driver.game_id, "e2e4", context(driver.owner, 0)) for driver in drivers)
        )
    finally:
        release.set()
        await pool.stop()

    # More callers than workers, so some turns get the controlled answer...
    assert any(result.status is TurnStatus.ENGINE_UNAVAILABLE for result in results)
    for result in results:
        # ...and every one of them still has the player's move safely stored.
        assert result.moves[0] == "e2e4"
        if result.status is TurnStatus.ENGINE_UNAVAILABLE:
            assert result.player_move == "e2e4"
            assert len(result.moves) == 1


def _blocking(release: threading.Event) -> EngineProcess:
    return BlockingProcess(release)


async def test_an_exhausted_pool_reports_itself_instead_of_queueing() -> None:
    release = threading.Event()
    settings = golden_settings(engine_pool_size=1)
    pool = StockfishPool(settings, process_factory=lambda: _blocking(release))
    await pool.start()

    board = chess.Board()
    try:
        held = asyncio.create_task(pool.best_move(board))
        await asyncio.sleep(0.05)
        with pytest.raises(EngineUnavailableError):
            # One worker, one permitted waiter: the third caller fails at once.
            await asyncio.gather(pool.best_move(board), pool.best_move(board))
        release.set()
        await held
    finally:
        release.set()
        await pool.stop()
