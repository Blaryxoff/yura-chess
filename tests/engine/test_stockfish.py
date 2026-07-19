"""Pool behaviour is verified with fake workers; the real binary is only used in smoke checks."""

import asyncio
import threading

import chess
import chess.engine
import pytest
from settings_fixtures import TEST_IDENTITY_SALT, UNREACHABLE_DATABASE_URL

from yura_chess.engine.stockfish import (
    EngineProcess,
    EngineSearchTimeoutError,
    EngineUnavailableError,
    StockfishPool,
)
from yura_chess.settings import Settings

pytestmark = pytest.mark.anyio


def engine_settings(**overrides: object) -> Settings:
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


class FakeProcess:
    """Records what the pool asked of it and can be told to block or fail."""

    def __init__(self, registry: "FakeRegistry") -> None:
        self.registry = registry
        self.closed = False

    def best_move(self, board: chess.Board, search_time: float) -> str:
        self.registry.searches.append(search_time)
        if self.registry.barrier is not None:
            self.registry.barrier.wait(timeout=2.0)
        if self.registry.block is not None:
            self.registry.block.wait(timeout=5.0)
        if self.registry.failure is not None:
            raise self.registry.failure
        return next(iter(board.legal_moves)).uci()

    def close(self) -> None:
        self.closed = True


class FakeRegistry:
    """Factory plus test switches shared by every process it hands out."""

    def __init__(self, spawn_error: Exception | None = None) -> None:
        self.processes: list[FakeProcess] = []
        self.searches: list[float] = []
        self.barrier: threading.Barrier | None = None
        self.block: threading.Event | None = None
        self.failure: Exception | None = None
        self.spawn_error = spawn_error

    def __call__(self) -> EngineProcess:
        if self.spawn_error is not None:
            raise self.spawn_error
        process = FakeProcess(self)
        self.processes.append(process)
        return process


async def test_pool_starts_two_independent_workers() -> None:
    registry = FakeRegistry()
    pool = StockfishPool(engine_settings(), registry)

    await pool.start()
    try:
        assert pool.ready_workers == 2
        assert len(registry.processes) == 2
    finally:
        await pool.stop()


async def test_two_users_are_served_concurrently() -> None:
    registry = FakeRegistry()
    registry.barrier = threading.Barrier(2)  # only two live workers can release it
    pool = StockfishPool(engine_settings(), registry)
    await pool.start()

    try:
        board = chess.Board()
        moves = await asyncio.gather(pool.best_move(board), pool.best_move(board))
    finally:
        await pool.stop()

    assert all(move in {m.uci() for m in chess.Board().legal_moves} for move in moves)


async def test_search_time_is_capped_by_the_deadline() -> None:
    registry = FakeRegistry()
    pool = StockfishPool(engine_settings(engine_move_deadline_seconds=0.2), registry)
    await pool.start()

    try:
        await pool.best_move(chess.Board(), search_time=5.0)
    finally:
        await pool.stop()

    assert registry.searches == [0.2]


async def test_saturated_pool_fails_fast_without_queueing() -> None:
    registry = FakeRegistry()
    registry.block = threading.Event()
    pool = StockfishPool(engine_settings(engine_pool_size=1), registry)
    await pool.start()

    board = chess.Board()
    busy = asyncio.create_task(pool.best_move(board))
    await asyncio.sleep(0.05)
    try:
        with pytest.raises(EngineUnavailableError):
            await pool.best_move(board)
    finally:
        registry.block.set()
        await busy
        await pool.stop()


async def test_search_timeout_restarts_only_the_damaged_worker() -> None:
    registry = FakeRegistry()
    registry.block = threading.Event()
    pool = StockfishPool(engine_settings(engine_pool_size=2), registry)
    await pool.start()

    try:
        with pytest.raises(EngineSearchTimeoutError):
            await pool.best_move(chess.Board())
        registry.block.set()
        registry.block = None
        for _ in range(200):
            if pool.ready_workers == 2:
                break
            await asyncio.sleep(0.01)
        assert pool.ready_workers == 2
        assert len(registry.processes) == 3  # the timed-out worker was respawned, its peer was not
    finally:
        registry.block = None
        await pool.stop()


async def test_a_dead_process_is_replaced_and_the_error_surfaces() -> None:
    registry = FakeRegistry()
    registry.failure = RuntimeError("engine process terminated")
    pool = StockfishPool(engine_settings(engine_pool_size=1), registry)
    await pool.start()

    try:
        with pytest.raises(RuntimeError, match="terminated"):
            await pool.best_move(chess.Board())
        registry.failure = None
        for _ in range(200):
            if pool.ready_workers == 1:
                break
            await asyncio.sleep(0.01)
        assert pool.ready_workers == 1
        assert await pool.best_move(chess.Board())
    finally:
        await pool.stop()


async def test_a_crashed_process_surfaces_as_the_error_callers_handle() -> None:
    registry = FakeRegistry()
    registry.failure = chess.engine.EngineTerminatedError("engine process died")
    pool = StockfishPool(engine_settings(engine_pool_size=1), registry)
    await pool.start()

    try:
        with pytest.raises(EngineUnavailableError):
            await pool.best_move(chess.Board())
    finally:
        registry.failure = None
        await pool.stop()


async def test_a_cancelled_search_does_not_leak_the_worker() -> None:
    registry = FakeRegistry()
    registry.block = threading.Event()
    pool = StockfishPool(engine_settings(engine_pool_size=1), registry)
    await pool.start()

    try:
        search = asyncio.ensure_future(pool.best_move(chess.Board()))
        await asyncio.sleep(0.05)
        search.cancel()
        with pytest.raises(asyncio.CancelledError):
            await search
        registry.block.set()
        registry.block = None
        for _ in range(200):
            if pool.ready_workers == 1:
                break
            await asyncio.sleep(0.01)
        assert pool.ready_workers == 1
        assert await pool.best_move(chess.Board())
    finally:
        if registry.block is not None:
            registry.block.set()
        registry.block = None
        await pool.stop()


async def test_a_missing_binary_leaves_the_application_startable() -> None:
    registry = FakeRegistry(spawn_error=FileNotFoundError("/usr/games/stockfish"))
    pool = StockfishPool(engine_settings(), registry)
    await pool.start()

    try:
        assert pool.ready_workers == 0
        with pytest.raises(EngineUnavailableError):
            await pool.best_move(chess.Board())
    finally:
        await pool.stop()


async def test_shutdown_closes_every_process_and_refuses_new_searches() -> None:
    registry = FakeRegistry()
    pool = StockfishPool(engine_settings(), registry)
    await pool.start()
    await pool.stop()

    assert [process.closed for process in registry.processes] == [True, True]
    assert pool.ready_workers == 0
    with pytest.raises(EngineUnavailableError):
        await pool.best_move(chess.Board())
