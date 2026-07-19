"""Bounded pool of independent Stockfish UCI processes.

Each worker owns one process and one lock, so a search never crosses workers.
Blocking UCI calls run outside the event loop; a damaged worker is restarted on
its own and stays out of the idle set until it is ready again.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from functools import partial
from typing import Protocol, TypeVar

import chess
import chess.engine
from starlette.concurrency import run_in_threadpool

from yura_chess.domain.analysis import PositionAnalysis, analysis_from_info
from yura_chess.settings import Settings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# The engine's own search limit is the real deadline; this only bounds the wait
# for a thread that ignored it, and such a thread makes its worker unusable.
_DEADLINE_GRACE_SECONDS = 0.25


class EngineUnavailableError(RuntimeError):
    """No ready worker could be handed out within the acquisition timeout."""


class EngineSearchTimeoutError(RuntimeError):
    """A worker did not produce a move within the search deadline."""


class EngineProcess(Protocol):
    """One UCI process. Every method blocks and runs off the event loop."""

    def best_move(self, board: chess.Board, search_time: float) -> str: ...

    def analyse(self, board: chess.Board, search_time: float, multipv: int) -> PositionAnalysis: ...

    def close(self) -> None: ...


ProcessFactory = Callable[[], EngineProcess]


class StockfishProcess:
    """`python-chess` UCI process configured once at spawn time."""

    def __init__(self, path: str, threads: int, hash_mb: int, skill_level: int) -> None:
        self._engine = chess.engine.SimpleEngine.popen_uci(path)
        self._engine.configure({"Threads": threads, "Hash": hash_mb, "Skill Level": skill_level})
        self._skill_level = skill_level

    def set_skill_level(self, skill_level: int) -> None:
        if skill_level == self._skill_level:
            return
        self._engine.configure({"Skill Level": skill_level})
        self._skill_level = skill_level

    def best_move(self, board: chess.Board, search_time: float) -> str:
        result = self._engine.play(board, chess.engine.Limit(time=search_time))
        if result.move is None:
            raise EngineSearchTimeoutError("engine returned no move")
        return result.move.uci()

    def analyse(self, board: chess.Board, search_time: float, multipv: int) -> PositionAnalysis:
        # The skill level stays as configured: an analysis must never change how
        # the same worker plays its next move.
        infos = self._engine.analyse(board, chess.engine.Limit(time=search_time), multipv=multipv)
        return analysis_from_info(board, list(infos) if isinstance(infos, list) else [infos])

    def close(self) -> None:
        self._engine.close()


def stockfish_factory(settings: Settings) -> ProcessFactory:
    def factory() -> EngineProcess:
        return StockfishProcess(
            path=str(settings.stockfish_path),
            threads=settings.engine_threads,
            hash_mb=settings.engine_hash_mb,
            skill_level=settings.engine_skill_level,
        )

    return factory


class _Worker:
    def __init__(self, index: int) -> None:
        self.index = index
        self._lock = threading.Lock()
        self._process: EngineProcess | None = None

    @property
    def is_ready(self) -> bool:
        return self._process is not None

    def spawn(self, factory: ProcessFactory) -> None:
        with self._lock:
            self._process = factory()

    def run(self, board: chess.Board, search_time: float, skill_level: int | None) -> str:
        with self._lock:
            process = self._ready()
            configure = getattr(process, "set_skill_level", None)
            if skill_level is not None and configure is not None:
                configure(skill_level)
            return process.best_move(board, search_time)

    def analyse(self, board: chess.Board, search_time: float, multipv: int) -> PositionAnalysis:
        with self._lock:
            return self._ready().analyse(board, search_time, multipv)

    def _ready(self) -> EngineProcess:
        process = self._process
        if process is None:
            raise EngineUnavailableError(f"worker {self.index} is not ready")
        return process

    def detach(self) -> EngineProcess | None:
        """Take the process out of rotation without waiting for a search still holding the lock."""
        process, self._process = self._process, None
        return process

    def close(self, process: EngineProcess) -> None:
        try:
            process.close()
        except Exception:  # noqa: BLE001 - a dead process must not break shutdown
            logger.warning("stockfish worker %s did not close cleanly", self.index, exc_info=True)


class StockfishPool:
    """Fixed number of workers, a bounded wait queue and a hard search deadline."""

    def __init__(self, settings: Settings, process_factory: ProcessFactory | None = None) -> None:
        self._settings = settings
        self._factory = process_factory or stockfish_factory(settings)
        self._workers = [_Worker(index) for index in range(settings.engine_pool_size)]
        self._idle: asyncio.Queue[_Worker] = asyncio.Queue()
        self._waiting = 0
        self._running = False
        self._background: set[asyncio.Task[None]] = set()

    @property
    def ready_workers(self) -> int:
        return sum(1 for worker in self._workers if worker.is_ready)

    @property
    def size(self) -> int:
        return len(self._workers)

    async def start(self) -> None:
        self._running = True
        for worker in self._workers:
            await self._spawn(worker)

    async def stop(self) -> None:
        self._running = False
        for task in list(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        self._background.clear()
        while not self._idle.empty():
            self._idle.get_nowait()
        for worker in self._workers:
            process = worker.detach()
            if process is not None:
                await run_in_threadpool(worker.close, process)

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        """Return the engine reply, or raise a controlled error well inside the Alice budget."""
        deadline = self._settings.engine_move_deadline_seconds
        limit = min(search_time if search_time is not None else self._settings.engine_move_time_seconds, deadline)
        worker = await self._acquire()
        position = board.copy(stack=False)
        return await self._guarded(worker, deadline, partial(worker.run, position, limit, skill_level))

    async def analyse(
        self,
        board: chess.Board,
        search_time: float | None = None,
        candidates: int | None = None,
    ) -> PositionAnalysis:
        """Value a copy of the position without touching the game or the worker's skill level."""
        deadline = self._settings.engine_analysis_deadline_seconds
        limit = min(search_time if search_time is not None else self._settings.engine_analysis_time_seconds, deadline)
        multipv = candidates if candidates is not None else self._settings.engine_analysis_candidates
        worker = await self._acquire()
        position = board.copy(stack=False)
        return await self._guarded(worker, deadline, partial(worker.analyse, position, limit, multipv))

    async def _guarded(self, worker: _Worker, deadline: float, call: Callable[[], _T]) -> _T:
        """Await one blocking worker call; any failure takes the worker out of rotation."""
        try:
            result = await asyncio.wait_for(
                run_in_threadpool(call),
                timeout=deadline + _DEADLINE_GRACE_SECONDS,
            )
        except TimeoutError:
            self._damage(worker)
            raise EngineSearchTimeoutError(f"no result within {deadline} s") from None
        except asyncio.CancelledError:
            # The caller's deadline cancelled the await, but the search thread
            # keeps running and keeps the worker's lock, so the worker may not
            # return to the idle set; only a restart makes it usable again.
            self._damage(worker)
            raise
        except chess.engine.EngineError as error:
            # A dead or misbehaving process has to reach callers as the failure
            # they already handle, not as a native type that escapes the adapter.
            self._damage(worker)
            raise EngineUnavailableError(f"engine worker failed: {type(error).__name__}") from error
        except Exception:
            self._damage(worker)
            raise
        self._idle.put_nowait(worker)
        return result

    async def _acquire(self) -> _Worker:
        if not self._running:
            raise EngineUnavailableError("engine pool is not running")
        try:
            return self._idle.get_nowait()
        except asyncio.QueueEmpty:
            pass
        # Bounded queue: past one waiter per worker the caller fails immediately.
        if self._waiting >= len(self._workers):
            raise EngineUnavailableError("engine pool is saturated")
        self._waiting += 1
        try:
            return await asyncio.wait_for(self._idle.get(), timeout=self._settings.engine_acquire_timeout_seconds)
        except TimeoutError:
            raise EngineUnavailableError(
                f"no engine worker within {self._settings.engine_acquire_timeout_seconds} s"
            ) from None
        finally:
            self._waiting -= 1

    async def _spawn(self, worker: _Worker) -> bool:
        try:
            await run_in_threadpool(worker.spawn, self._factory)
        except Exception:  # noqa: BLE001 - a missing binary must not stop the application
            logger.warning("stockfish worker %s failed to start", worker.index, exc_info=True)
            return False
        self._idle.put_nowait(worker)
        return True

    def _damage(self, worker: _Worker) -> None:
        """Take the worker out of rotation; it returns to the idle set only once respawned."""
        process = worker.detach()
        if not self._running:
            return
        task = asyncio.create_task(self._restart(worker, process))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _restart(self, worker: _Worker, process: EngineProcess | None) -> None:
        if process is not None:
            await run_in_threadpool(worker.close, process)
        while self._running:
            if await self._spawn(worker):
                return
            await asyncio.sleep(self._settings.engine_restart_delay_seconds)
