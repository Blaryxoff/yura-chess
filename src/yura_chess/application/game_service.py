"""The chess lifecycle: every request is owner-checked, idempotent and resumable.

A turn spans two short transactions with the engine search strictly between them:

* **A** — verify owner, revision and replay key, apply the player's move and
  record the debt for the engine reply (`pending_engine_turn`).
* the search runs with no database transaction open;
* **B** — verify owner, revision and pending token, apply the engine reply and
  store the final response atomically with it.

A repeat of a request either returns the response stored by B or resumes the
debt recorded by A. The player's move is never applied twice.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

import chess
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.domain.game import EngineSettings, GameMode, GameState, GameStatus, PlayerColor
from yura_chess.domain.results import (
    GameEnd,
    GameOutcome,
    TurnResult,
    TurnStatus,
    automatic_outcome,
    claimable_draw,
)
from yura_chess.engine.stockfish import EngineSearchTimeoutError, EngineUnavailableError
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import RequestReplayRow

logger = logging.getLogger(__name__)


class MoveSearch(Protocol):
    """The engine capability this service needs; `StockfishPool` satisfies it."""

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str: ...


class PlayerMoveObserver(Protocol):
    """Notified after a player move is committed without delaying the reply.

    It runs with no transaction open and must never raise: what it records is a
    cache, while the turn it watches has already happened.
    """

    async def observe_player_move(self, owner_key: str, state: GameState, ply: int, move_uci: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The composite replay key plus the fingerprint of the significant fields."""

    skill_id: str
    session_id: str
    message_id: str
    fingerprint: str
    is_new_session: bool = False
    timezone: str | None = None
    traffic_source: Literal["real", "test"] = "real"


class GameService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: MoveSearch,
        observer: PlayerMoveObserver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._observer = observer
        self._background: set[asyncio.Task[None]] = set()

    async def start_game(
        self,
        owner_key: str,
        request: RequestContext,
        player_color: PlayerColor = PlayerColor.WHITE,
        engine: EngineSettings | None = None,
        mode: GameMode = GameMode.GAME,
    ) -> TurnResult:
        """Create a game; when the player takes Black the engine opens the position."""
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            replay, created = self._claim(repository, request, owner_key)
            if created:
                repository.resign_active_games(owner_key)
                state = repository.create_game(owner_key, player_color, engine, mode=mode)
                replay.game_id = state.id
            else:
                # A retried start must reuse the game the first delivery created.
                if replay.response_payload is not None:
                    return TurnResult.from_payload(replay.response_payload)
                if replay.game_id is None:
                    raise LookupError("claimed start request has neither a response nor a game")
                state = repository.load(replay.game_id, owner_key)
            if not self._engine_to_move(state):
                return self._finalize(repository, replay, TurnResult.from_state(state, TurnStatus.OK))
            # The engine opens: the debt is owed by the position, not by a pending row.
            pending = state.pending_engine_turn
        return await self._play_engine_move(
            owner_key,
            state,
            token=pending.token if pending else None,
            player_move=pending.player_move_uci if pending else None,
            request=request,
        )

    async def continue_game(self, owner_key: str, game_id: str, request: RequestContext) -> TurnResult:
        """Report the position, resuming an engine reply that a previous request left owed."""
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            state = repository.load(game_id, owner_key)
            replay, created = self._claim(repository, request, owner_key, game_id)
            if not created and replay.response_payload is not None:
                return TurnResult.from_payload(replay.response_payload)
            if not self._engine_to_move(state):
                return self._finalize(repository, replay, TurnResult.from_state(state, TurnStatus.OK))
            pending = state.pending_engine_turn
        return await self._play_engine_move(
            owner_key,
            state,
            token=pending.token if pending else None,
            player_move=pending.player_move_uci if pending else None,
            request=request,
        )

    def load_game(self, owner_key: str, game_id: str) -> GameState:
        """Load the current owner-scoped state without claiming or mutating a request."""
        with session_scope(self._session_factory) as session:
            return GameRepository(session).load(game_id, owner_key)

    def find_latest_active_game(self, owner_key: str) -> GameState | None:
        """Find the unfinished game most recently advanced by this player."""
        with session_scope(self._session_factory) as session:
            return GameRepository(session).find_latest_active(owner_key)

    def find_latest_game(self, owner_key: str) -> GameState | None:
        """Find the most recent game of this player, finished ones included."""
        with session_scope(self._session_factory) as session:
            return GameRepository(session).find_latest(owner_key)

    def find_latest_finished_game(self, owner_key: str) -> GameState | None:
        """Find the latest game that can be reviewed."""
        with session_scope(self._session_factory) as session:
            return GameRepository(session).find_latest_finished(owner_key)

    def request_was_seen(self, owner_key: str, request: RequestContext) -> bool:
        """Check a replay key before conversation-only behavior can bypass it."""
        with session_scope(self._session_factory) as session:
            return GameRepository(session).request_was_seen(
                request.skill_id,
                request.session_id,
                request.message_id,
                request.fingerprint,
                owner_key,
            )

    def cached_alice_response(self, owner_key: str, request: RequestContext) -> str | None:
        with session_scope(self._session_factory) as session:
            replay = GameRepository(session).get_request_replay(
                request.skill_id,
                request.session_id,
                request.message_id,
                request.fingerprint,
                owner_key,
            )
            return replay.alice_response_payload if replay is not None else None

    def store_alice_response(
        self,
        owner_key: str,
        request: RequestContext,
        response_payload: str,
        game_id: str | None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            replay, _ = self._claim(repository, request, owner_key, game_id)
            repository.store_alice_response(replay, response_payload, game_id)

    async def resume_request(self, owner_key: str, request: RequestContext) -> TurnResult | None:
        """Resume a claimed turn before state-dependent speech routing can reinterpret it."""
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            replay = repository.get_request_replay(
                request.skill_id,
                request.session_id,
                request.message_id,
                request.fingerprint,
                owner_key,
            )
            if replay is None or replay.alice_response_payload is not None:
                return None
            if replay.response_payload is not None:
                return TurnResult.from_payload(replay.response_payload)
            if replay.game_id is None:
                return None
            state = repository.load(replay.game_id, owner_key)
            if not self._engine_to_move(state):
                return self._finalize(repository, replay, TurnResult.from_state(state, TurnStatus.OK))
            pending = state.pending_engine_turn
        return await self._play_engine_move(
            owner_key,
            state,
            pending.token if pending else None,
            pending.player_move_uci if pending else None,
            request,
        )

    async def play_move(
        self,
        owner_key: str,
        game_id: str,
        move_uci: str,
        request: RequestContext,
    ) -> TurnResult:
        terminal_result: TurnResult | None = None
        token: str | None = None
        player_move: str | None = None
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            state = repository.load(game_id, owner_key)
            replay, created = self._claim(repository, request, owner_key, game_id)
            if not created and replay.response_payload is not None:
                return TurnResult.from_payload(replay.response_payload)

            # Which ply the optional observer is owed. Re-observation is safe:
            # checkpoints are idempotent, and a retry may be the first request
            # that survives long enough to schedule it.
            observed: int | None = None
            if self._engine_to_move(state):
                # An earlier move of this game is still owed an answer. Resume it
                # rather than push a second move onto the same position.
                pending = state.pending_engine_turn
                token = pending.token if pending else None
                player_move = pending.player_move_uci if pending else None
                observed = len(state.moves) - 1 if player_move is not None else None
            else:
                if state.status is not GameStatus.ACTIVE:
                    return self._finalize(
                        repository, replay, TurnResult.from_state(state, TurnStatus.GAME_ALREADY_FINISHED)
                    )
                board = state.board()
                rejection = self._reject_move(state, board, move_uci)
                if rejection is not None:
                    return self._finalize(repository, replay, rejection)

                board.push(chess.Move.from_uci(move_uci))
                outcome = automatic_outcome(board)
                if outcome is not None:
                    # The player ended the game: there is no engine reply to owe.
                    state = repository.append_moves(
                        game_id, owner_key, state.revision, (move_uci,), status=GameStatus.FINISHED
                    )
                    result = TurnResult.from_state(
                        state, TurnStatus.GAME_OVER, board=board, player_move=move_uci, outcome=outcome
                    )
                    terminal_result = self._finalize(repository, replay, result)
                    player_move = move_uci
                    observed = len(state.moves) - 1

                if terminal_result is None:
                    token = str(uuid.uuid4())
                    player_move = move_uci
                    state = repository.begin_engine_turn(game_id, owner_key, state.revision, move_uci, token)
                    observed = len(state.moves) - 1
        if terminal_result is not None:
            if player_move is not None and observed is not None:
                self._schedule_observe(owner_key, state, observed, player_move)
                await asyncio.sleep(0)
            return terminal_result
        if player_move is not None and observed is not None:
            self._schedule_observe(owner_key, state, observed, player_move)
        result = await self._play_engine_move(owner_key, state, token, player_move, request)
        # Give a fast observer one event-loop turn to persist its checkpoint,
        # without waiting for slow analysis before answering Alice.
        await asyncio.sleep(0)
        return result

    async def resign(self, owner_key: str, game_id: str, request: RequestContext) -> TurnResult:
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            state = repository.load(game_id, owner_key)
            replay, created = self._claim(repository, request, owner_key, game_id)
            if not created and replay.response_payload is not None:
                return TurnResult.from_payload(replay.response_payload)
            if state.status is not GameStatus.ACTIVE:
                return self._finalize(
                    repository, replay, TurnResult.from_state(state, TurnStatus.GAME_ALREADY_FINISHED)
                )
            if state.pending_engine_turn is not None:
                # Resigning outranks the owed reply; drop the debt so nothing resumes it.
                state = repository.clear_pending_engine_turn(game_id, owner_key, state.revision)
            state = repository.append_moves(game_id, owner_key, state.revision, (), status=GameStatus.RESIGNED)
            winner = PlayerColor.BLACK if state.player_color is PlayerColor.WHITE else PlayerColor.WHITE
            result = TurnResult.from_state(
                state, TurnStatus.GAME_OVER, outcome=GameOutcome(GameEnd.RESIGNATION, winner)
            )
            return self._finalize(repository, replay, result)

    async def claim_draw(self, owner_key: str, game_id: str, request: RequestContext) -> TurnResult:
        """The fifty-move and threefold draws apply only when the player demands them."""
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            state = repository.load(game_id, owner_key)
            replay, created = self._claim(repository, request, owner_key, game_id)
            if not created and replay.response_payload is not None:
                return TurnResult.from_payload(replay.response_payload)
            if state.status is not GameStatus.ACTIVE:
                return self._finalize(
                    repository, replay, TurnResult.from_state(state, TurnStatus.GAME_ALREADY_FINISHED)
                )
            end = claimable_draw(state.board())
            if end is None:
                return self._finalize(repository, replay, TurnResult.from_state(state, TurnStatus.DRAW_NOT_CLAIMABLE))
            if state.pending_engine_turn is not None:
                state = repository.clear_pending_engine_turn(game_id, owner_key, state.revision)
            state = repository.append_moves(game_id, owner_key, state.revision, (), status=GameStatus.FINISHED)
            result = TurnResult.from_state(state, TurnStatus.GAME_OVER, outcome=GameOutcome(end))
            return self._finalize(repository, replay, result)

    async def undo_turn(self, owner_key: str, game_id: str, request: RequestContext) -> TurnResult:
        """Take back the player's last move together with the engine's answer to it."""
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            state = repository.load(game_id, owner_key)
            replay, created = self._claim(repository, request, owner_key, game_id)
            if not created and replay.response_payload is not None:
                return TurnResult.from_payload(replay.response_payload)
            if state.pending_engine_turn is not None:
                return self._finalize(
                    repository,
                    replay,
                    TurnResult.from_state(state, TurnStatus.UNDO_REJECTED, detail="engine turn in progress"),
                )
            if state.status is not GameStatus.ACTIVE:
                return self._finalize(
                    repository, replay, TurnResult.from_state(state, TurnStatus.GAME_ALREADY_FINISHED)
                )
            keep = self._last_player_ply(state)
            if keep is None:
                return self._finalize(
                    repository,
                    replay,
                    TurnResult.from_state(state, TurnStatus.UNDO_REJECTED, detail="nothing to take back"),
                )
            state = repository.truncate_moves(game_id, owner_key, state.revision, keep)
            return self._finalize(repository, replay, TurnResult.from_state(state, TurnStatus.OK))

    async def _observe(self, owner_key: str, state: GameState, ply: int, move_uci: str) -> None:
        """Let the observer value the move; its failure never costs the turn."""
        if self._observer is None:
            return
        try:
            await self._observer.observe_player_move(owner_key, state, ply, move_uci)
        except Exception:  # noqa: BLE001 - the move is already played and must stand
            logger.warning("player move observer failed for game %s ply %s", state.id, ply, exc_info=True)

    def _schedule_observe(self, owner_key: str, state: GameState, ply: int, move_uci: str) -> None:
        if self._observer is None:
            return
        task = asyncio.create_task(
            self._observe(owner_key, state, ply, move_uci),
            name=f"observe-player-move-{state.id}-{ply}",
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _play_engine_move(
        self,
        owner_key: str,
        state: GameState,
        token: str | None,
        player_move: str | None,
        request: RequestContext,
    ) -> TurnResult:
        """Search with no transaction open, then settle the turn in transaction B."""
        try:
            engine_move = await self._engine.best_move(
                state.board(),
                state.engine.move_time_ms / 1000,
                skill_level=state.engine.skill_level,
            )
        except (EngineUnavailableError, EngineSearchTimeoutError) as error:
            # The debt stays recorded and no response is stored, so the next
            # request searches again instead of repeating the player's move.
            return TurnResult.from_state(
                state,
                TurnStatus.ENGINE_UNAVAILABLE,
                player_move=player_move,
                detail=type(error).__name__,
            )

        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            replay, _ = self._claim(repository, request, owner_key, state.id)
            if replay.response_payload is not None:
                # A concurrent retry already settled this turn.
                return TurnResult.from_payload(replay.response_payload)
            current = repository.load(state.id, owner_key)
            if current.revision != state.revision:
                # Another writer moved the game on; report what is true now
                # instead of applying a reply computed for a stale position. The
                # replay still has to be finalized or the same delivery searches
                # again and can produce a different answer.
                if current.pending_engine_turn is not None:
                    result = TurnResult.from_state(
                        current,
                        TurnStatus.ENGINE_UNAVAILABLE,
                        player_move=player_move,
                        detail="position changed while the engine was searching",
                    )
                else:
                    result = TurnResult.from_state(
                        current,
                        TurnStatus.OK if current.status is GameStatus.ACTIVE else TurnStatus.GAME_ALREADY_FINISHED,
                        player_move=player_move,
                    )
                return self._finalize(repository, replay, result)

            board = current.board()
            board.push(chess.Move.from_uci(engine_move))
            outcome = automatic_outcome(board)
            current = repository.finish_engine_turn(
                state.id,
                owner_key,
                current.revision,
                token,
                engine_move,
                status=GameStatus.FINISHED if outcome else None,
            )
            result = TurnResult.from_state(
                current,
                TurnStatus.GAME_OVER if outcome else TurnStatus.OK,
                board=board,
                player_move=player_move,
                engine_move=engine_move,
                outcome=outcome,
            )
            return self._finalize(repository, replay, result)

    @staticmethod
    def _claim(
        repository: GameRepository,
        request: RequestContext,
        owner_key: str,
        game_id: str | None = None,
    ) -> tuple[RequestReplayRow, bool]:
        return repository.record_request(
            request.skill_id,
            request.session_id,
            request.message_id,
            request.fingerprint,
            owner_key,
            game_id,
            request.traffic_source,
        )

    @staticmethod
    def _finalize(repository: GameRepository, replay: RequestReplayRow, result: TurnResult) -> TurnResult:
        repository.store_response(replay, result.to_payload(), result.game_id)
        return result

    @staticmethod
    def _engine_to_move(state: GameState) -> bool:
        if state.status is not GameStatus.ACTIVE:
            return False
        board = state.board()
        return board.turn != state.player_color.to_chess() and automatic_outcome(board) is None

    @staticmethod
    def _reject_move(state: GameState, board: chess.Board, move_uci: str) -> TurnResult | None:
        if board.turn != state.player_color.to_chess():
            return TurnResult.from_state(state, TurnStatus.NOT_PLAYER_TURN, board=board)
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError:
            return TurnResult.from_state(state, TurnStatus.ILLEGAL_MOVE, board=board, detail=move_uci)
        if move not in board.legal_moves:
            return TurnResult.from_state(state, TurnStatus.ILLEGAL_MOVE, board=board, detail=move_uci)
        return None

    @staticmethod
    def _last_player_ply(state: GameState) -> int | None:
        """Index of the player's last move, i.e. how many plies survive an undo."""
        player_moves_first = state.player_color.to_chess() == chess.Board(state.initial_fen).turn
        for ply in range(len(state.moves) - 1, -1, -1):
            if (ply % 2 == 0) is player_moves_first:
                return ply
        return None
