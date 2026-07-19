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

import uuid
from dataclasses import dataclass
from typing import Protocol

import chess
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.domain.game import EngineSettings, GameState, GameStatus, PlayerColor
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


class MoveSearch(Protocol):
    """The engine capability this service needs; `StockfishPool` satisfies it."""

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The composite replay key plus the fingerprint of the significant fields."""

    skill_id: str
    session_id: str
    message_id: str
    fingerprint: str
    is_new_session: bool = False
    timezone: str | None = None


class GameService:
    def __init__(self, session_factory: sessionmaker[Session], engine: MoveSearch) -> None:
        self._session_factory = session_factory
        self._engine = engine

    async def start_game(
        self,
        owner_key: str,
        request: RequestContext,
        player_color: PlayerColor = PlayerColor.WHITE,
        engine: EngineSettings | None = None,
    ) -> TurnResult:
        """Create a game; when the player takes Black the engine opens the position."""
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            replay, created = self._claim(repository, request, owner_key)
            if created:
                state = repository.create_game(owner_key, player_color, engine)
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

    async def play_move(
        self,
        owner_key: str,
        game_id: str,
        move_uci: str,
        request: RequestContext,
    ) -> TurnResult:
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            state = repository.load(game_id, owner_key)
            replay, created = self._claim(repository, request, owner_key, game_id)
            if not created and replay.response_payload is not None:
                return TurnResult.from_payload(replay.response_payload)

            if self._engine_to_move(state):
                # An earlier move of this game is still owed an answer. Resume it
                # rather than push a second move onto the same position.
                pending = state.pending_engine_turn
                token = pending.token if pending else None
                player_move = pending.player_move_uci if pending else None
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
                    return self._finalize(repository, replay, result)

                token = str(uuid.uuid4())
                player_move = move_uci
                state = repository.begin_engine_turn(game_id, owner_key, state.revision, move_uci, token)
        return await self._play_engine_move(owner_key, state, token, player_move, request)

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
