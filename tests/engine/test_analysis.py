"""Analysis conversion is tested against real `python-chess` scores; the pool is tested with fakes."""

import asyncio
import threading

import chess
import chess.engine
import pytest
from settings_fixtures import TEST_IDENTITY_SALT, UNREACHABLE_DATABASE_URL

from yura_chess.domain.analysis import (
    MATE_CENTIPAWNS,
    PositionAnalysis,
    Score,
    analysis_from_info,
)
from yura_chess.domain.game import PlayerColor
from yura_chess.engine.stockfish import (
    EngineProcess,
    EngineSearchTimeoutError,
    EngineUnavailableError,
    StockfishPool,
)
from yura_chess.settings import Settings

pytestmark = pytest.mark.anyio


def analysis_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": UNREACHABLE_DATABASE_URL,
        "identity_salt": TEST_IDENTITY_SALT,
        "engine_pool_size": 2,
        "engine_acquire_timeout_seconds": 0.1,
        "engine_move_deadline_seconds": 0.2,
        "engine_move_time_seconds": 0.05,
        "engine_analysis_deadline_seconds": 0.2,
        "engine_analysis_time_seconds": 0.05,
        "engine_restart_delay_seconds": 0.01,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def info(pv: list[str], score: chess.engine.Score, turn: chess.Color, depth: int = 12) -> dict[str, object]:
    return {
        "score": chess.engine.PovScore(score, turn),
        "pv": [chess.Move.from_uci(uci) for uci in pv],
        "depth": depth,
    }


class FakeProcess:
    """Answers both contracts and can be told to block or fail during analysis."""

    def __init__(self, registry: "FakeRegistry") -> None:
        self.registry = registry
        self.closed = False

    def best_move(self, board: chess.Board, search_time: float) -> str:
        return next(iter(board.legal_moves)).uci()

    def analyse(self, board: chess.Board, search_time: float, multipv: int) -> PositionAnalysis:
        self.registry.requests.append((search_time, multipv))
        if self.registry.block is not None:
            self.registry.block.wait(timeout=5.0)
        if self.registry.failure is not None:
            raise self.registry.failure
        return analysis_from_info(
            board,
            [info([move.uci()], chess.engine.Cp(10), board.turn) for move in list(board.legal_moves)[:multipv]],
        )

    def close(self) -> None:
        self.closed = True


class FakeRegistry:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []
        self.requests: list[tuple[float, int]] = []
        self.block: threading.Event | None = None
        self.failure: Exception | None = None

    def __call__(self) -> EngineProcess:
        process = FakeProcess(self)
        self.processes.append(process)
        return process


def test_score_keeps_one_meaning() -> None:
    with pytest.raises(ValueError):
        Score()
    with pytest.raises(ValueError):
        Score(centipawns=10, mate_in=2)
    with pytest.raises(ValueError):
        Score(mate_in=0)


def test_mate_outranks_any_material_score() -> None:
    assert Score(mate_in=3).as_centipawns() > Score(centipawns=5000).as_centipawns()
    assert Score(mate_in=1).as_centipawns() > Score(mate_in=5).as_centipawns()
    assert Score(mate_in=-1).as_centipawns() < Score(centipawns=-5000).as_centipawns()
    assert Score(mate_in=2).inverted() == Score(mate_in=-2)
    assert Score(centipawns=120).inverted() == Score(centipawns=-120)


def test_analysis_is_read_from_the_side_to_move() -> None:
    board = chess.Board()
    board.push_uci("e2e4")  # black to move

    analysis = analysis_from_info(board, [info(["e7e5", "g1f3"], chess.engine.Cp(-40), board.turn)])

    assert analysis.fen == board.fen()
    assert analysis.side_to_move is PlayerColor.BLACK
    assert analysis.depth == 12
    assert analysis.best is not None
    assert analysis.best.move == "e7e5"
    assert analysis.best.principal_variation == ("e7e5", "g1f3")
    assert analysis.score == Score(centipawns=-40)
    assert analysis.score_for(PlayerColor.BLACK) == Score(centipawns=-40)
    assert analysis.score_for(PlayerColor.WHITE) == Score(centipawns=40)


def test_mate_distance_survives_the_conversion() -> None:
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")

    analysis = analysis_from_info(board, [info(["a1a8"], chess.engine.Mate(1), board.turn)])

    assert analysis.score == Score(mate_in=1)
    assert analysis.score_for(PlayerColor.BLACK) == Score(mate_in=-1)
    assert analysis.score_for(PlayerColor.WHITE).as_centipawns() == MATE_CENTIPAWNS - 1


def test_candidates_are_ordered_best_first() -> None:
    board = chess.Board()

    analysis = analysis_from_info(
        board,
        [
            info(["b1c3"], chess.engine.Cp(15), board.turn),
            info(["e2e4"], chess.engine.Mate(6), board.turn),
            info(["a2a3"], chess.engine.Cp(60), board.turn),
        ],
    )

    assert [candidate.move for candidate in analysis.candidates] == ["e2e4", "a2a3", "b1c3"]


def test_a_line_without_moves_is_dropped() -> None:
    board = chess.Board()

    analysis = analysis_from_info(board, [info([], chess.engine.Cp(15), board.turn)])

    assert analysis.candidates == ()
    assert analysis.best is None
    assert analysis.score is None
    assert analysis.score_for(PlayerColor.WHITE) is None


async def test_analysis_asks_for_the_requested_number_of_candidates() -> None:
    registry = FakeRegistry()
    pool = StockfishPool(analysis_settings(engine_analysis_candidates=3), registry)
    await pool.start()

    try:
        analysis = await pool.analyse(chess.Board())
    finally:
        await pool.stop()

    assert registry.requests == [(0.05, 3)]
    assert len(analysis.candidates) == 3


async def test_analysis_time_is_capped_by_its_own_deadline() -> None:
    registry = FakeRegistry()
    pool = StockfishPool(analysis_settings(engine_analysis_deadline_seconds=0.2), registry)
    await pool.start()

    try:
        await pool.analyse(chess.Board(), search_time=5.0, candidates=1)
    finally:
        await pool.stop()

    assert registry.requests == [(0.2, 1)]


async def test_analysis_does_not_touch_the_position_it_was_given() -> None:
    registry = FakeRegistry()
    pool = StockfishPool(analysis_settings(), registry)
    await pool.start()
    board = chess.Board()
    board.push_uci("e2e4")
    before = board.fen()

    try:
        await pool.analyse(board)
    finally:
        await pool.stop()

    assert board.fen() == before
    assert board.move_stack[-1].uci() == "e2e4"


async def test_a_saturated_pool_refuses_analysis_without_queueing() -> None:
    registry = FakeRegistry()
    registry.block = threading.Event()
    pool = StockfishPool(analysis_settings(engine_pool_size=1), registry)
    await pool.start()

    busy = asyncio.create_task(pool.analyse(chess.Board()))
    await asyncio.sleep(0.05)
    try:
        with pytest.raises(EngineUnavailableError):
            await pool.analyse(chess.Board())
    finally:
        registry.block.set()
        await busy
        await pool.stop()


async def test_an_analysis_timeout_leaves_the_game_playable() -> None:
    registry = FakeRegistry()
    registry.block = threading.Event()
    pool = StockfishPool(analysis_settings(engine_pool_size=1), registry)
    await pool.start()

    try:
        with pytest.raises(EngineSearchTimeoutError):
            await pool.analyse(chess.Board())
        registry.block.set()
        registry.block = None
        for _ in range(200):
            if pool.ready_workers == 1:
                break
            await asyncio.sleep(0.01)
        assert pool.ready_workers == 1
        assert await pool.best_move(chess.Board())  # the ordinary move contract still works
    finally:
        if registry.block is not None:
            registry.block.set()
        await pool.stop()


async def test_a_cancelled_analysis_does_not_leak_the_worker() -> None:
    registry = FakeRegistry()
    registry.block = threading.Event()
    pool = StockfishPool(analysis_settings(engine_pool_size=1), registry)
    await pool.start()

    try:
        pending = asyncio.ensure_future(pool.analyse(chess.Board()))
        await asyncio.sleep(0.05)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        registry.block.set()
        registry.block = None
        for _ in range(200):
            if pool.ready_workers == 1:
                break
            await asyncio.sleep(0.01)
        assert pool.ready_workers == 1
        assert await pool.analyse(chess.Board())
    finally:
        if registry.block is not None:
            registry.block.set()
        await pool.stop()


async def test_a_crashed_process_surfaces_as_the_error_callers_handle() -> None:
    registry = FakeRegistry()
    registry.failure = chess.engine.EngineTerminatedError("engine process died")
    pool = StockfishPool(analysis_settings(engine_pool_size=1), registry)
    await pool.start()

    try:
        with pytest.raises(EngineUnavailableError):
            await pool.analyse(chess.Board())
    finally:
        registry.failure = None
        await pool.stop()


async def test_a_stopped_pool_refuses_analysis() -> None:
    registry = FakeRegistry()
    pool = StockfishPool(analysis_settings(), registry)
    await pool.start()
    await pool.stop()

    with pytest.raises(EngineUnavailableError):
        await pool.analyse(chess.Board())
