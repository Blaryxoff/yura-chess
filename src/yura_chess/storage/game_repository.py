"""Repository for games, UCI history and Alice replay records.

Every read and write requires the owner key: a game is never loaded or modified
by `game_id` alone, so a foreign or forged Alice state cannot reveal or touch it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import chess
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yura_chess.domain.game import (
    START_FEN,
    EngineSettings,
    GameState,
    GameStatus,
    MoveActor,
    PendingEngineTurn,
    PlayerColor,
)
from yura_chess.storage.models import (
    GameMoveRow,
    GameRow,
    PendingEngineTurnRow,
    RequestReplayRow,
)


class GameNotFoundError(LookupError):
    """No game with this id belongs to this owner."""


class RevisionConflictError(RuntimeError):
    """The game changed between read and write; the caller must reload."""


class ReplayFingerprintConflictError(RuntimeError):
    """The replay key was reused with different significant request fields."""


class PendingTurnConflictError(RuntimeError):
    """The game already has an unfinished engine turn."""


class PendingTurnMismatchError(RuntimeError):
    """The engine turn being finished is not the one the game is waiting for."""


def _to_state(row: GameRow) -> GameState:
    pending = row.pending_engine_turn
    return GameState(
        id=row.id,
        owner_key=row.owner_key,
        status=GameStatus(row.status),
        player_color=PlayerColor(row.player_color),
        initial_fen=row.initial_fen,
        moves=tuple(move.uci for move in row.moves),
        revision=row.revision,
        engine=EngineSettings(skill_level=row.engine_skill_level, move_time_ms=row.engine_move_time_ms),
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_player_move_at=row.last_player_move_at,
        pending_engine_turn=(
            PendingEngineTurn(token=pending.token, player_move_uci=pending.player_move_uci) if pending else None
        ),
    )


class GameRepository:
    """Thin data-access layer bound to one short transaction (one Session)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_game(
        self,
        owner_key: str,
        player_color: PlayerColor,
        engine: EngineSettings | None = None,
        initial_fen: str = START_FEN,
    ) -> GameState:
        engine = engine or EngineSettings()
        row = GameRow(
            id=str(uuid.uuid4()),
            owner_key=owner_key,
            status=GameStatus.ACTIVE.value,
            player_color=player_color.value,
            initial_fen=initial_fen,
            revision=1,
            engine_skill_level=engine.skill_level,
            engine_move_time_ms=engine.move_time_ms,
        )
        self._session.add(row)
        self._session.flush()
        return _to_state(row)

    def load(self, game_id: str, owner_key: str) -> GameState:
        return _to_state(self._load_row(game_id, owner_key))

    def find(self, game_id: str, owner_key: str) -> GameState | None:
        """Owner mismatch and unknown id are indistinguishable by design."""
        try:
            return self.load(game_id, owner_key)
        except GameNotFoundError:
            return None

    def find_latest_active(self, owner_key: str) -> GameState | None:
        """Return the most recently played unfinished game for this owner.

        A game with no player move is ordered after every game the player
        actually moved in, even when that empty game was created later.
        """
        statement = (
            select(GameRow)
            .where(GameRow.owner_key == owner_key, GameRow.status == GameStatus.ACTIVE.value)
            .order_by(
                GameRow.last_player_move_at.is_(None),
                GameRow.last_player_move_at.desc(),
                GameRow.created_at.desc(),
            )
            .limit(1)
        )
        row = self._session.scalars(statement).one_or_none()
        return _to_state(row) if row is not None else None

    def append_moves(
        self,
        game_id: str,
        owner_key: str,
        expected_revision: int,
        moves: tuple[str, ...],
        status: GameStatus | None = None,
    ) -> GameState:
        row = self._load_row(game_id, owner_key, expected_revision)
        next_ply = len(row.moves)
        for offset, uci in enumerate(moves):
            ply = next_ply + offset
            actor = self._actor_for_ply(row, ply)
            row.moves.append(self._move_row(row, ply, uci, actor))
        if status is not None:
            row.status = status.value
        return self._bump_revision(row)

    def truncate_moves(
        self,
        game_id: str,
        owner_key: str,
        expected_revision: int,
        keep_plies: int,
    ) -> GameState:
        """Drop the tail of the UCI history; the remaining prefix stays canonical."""
        row = self._load_row(game_id, owner_key, expected_revision)
        if row.pending_engine_turn is not None:
            raise PendingTurnConflictError(f"game {game_id} has a pending engine turn")
        del row.moves[keep_plies:]
        row.last_player_move_at = max(
            (move.created_at for move in row.moves if move.actor == MoveActor.PLAYER.value),
            default=None,
        )
        return self._bump_revision(row)

    def begin_engine_turn(
        self,
        game_id: str,
        owner_key: str,
        expected_revision: int,
        player_move_uci: str,
        token: str,
    ) -> GameState:
        """Transaction A: store the player's move and the debt for the engine reply in one bump."""
        row = self._load_row(game_id, owner_key, expected_revision)
        if row.pending_engine_turn is not None:
            raise PendingTurnConflictError(f"game {game_id} already has a pending engine turn")
        row.moves.append(self._move_row(row, len(row.moves), player_move_uci, MoveActor.PLAYER))
        row.pending_engine_turn = PendingEngineTurnRow(
            game_id=row.id,
            token=token,
            player_move_uci=player_move_uci,
        )
        return self._bump_revision(row)

    def finish_engine_turn(
        self,
        game_id: str,
        owner_key: str,
        expected_revision: int,
        token: str | None,
        engine_move_uci: str,
        status: GameStatus | None = None,
    ) -> GameState:
        """Transaction B: settle the debt recorded by `token` and store the engine reply.

        `token` is `None` only for the engine's opening move, which is owed by the
        position itself rather than by a preceding player move.
        """
        row = self._load_row(game_id, owner_key, expected_revision)
        pending = row.pending_engine_turn
        if token is None:
            if pending is not None:
                raise PendingTurnMismatchError(f"game {game_id} is waiting for another engine turn")
        elif pending is None or pending.token != token:
            raise PendingTurnMismatchError(f"game {game_id} is not waiting for engine turn {token}")
        row.pending_engine_turn = None
        row.moves.append(self._move_row(row, len(row.moves), engine_move_uci, MoveActor.ENGINE))
        if status is not None:
            row.status = status.value
        return self._bump_revision(row)

    def set_pending_engine_turn(
        self,
        game_id: str,
        owner_key: str,
        expected_revision: int,
        token: str,
        player_move_uci: str,
    ) -> GameState:
        row = self._load_row(game_id, owner_key, expected_revision)
        if row.pending_engine_turn is not None:
            raise PendingTurnConflictError(f"game {game_id} already has a pending engine turn")
        row.pending_engine_turn = PendingEngineTurnRow(
            game_id=row.id,
            token=token,
            player_move_uci=player_move_uci,
        )
        return self._bump_revision(row)

    def clear_pending_engine_turn(self, game_id: str, owner_key: str, expected_revision: int) -> GameState:
        row = self._load_row(game_id, owner_key, expected_revision)
        row.pending_engine_turn = None
        return self._bump_revision(row)

    def request_was_seen(
        self,
        skill_id: str,
        session_id: str,
        message_id: str,
        request_fingerprint: str,
        owner_key: str,
    ) -> bool:
        existing = self._find_replay(skill_id, session_id, message_id)
        if existing is None:
            return False
        self._verify_replay(existing, request_fingerprint, owner_key)
        return True

    def get_request_replay(
        self,
        skill_id: str,
        session_id: str,
        message_id: str,
        request_fingerprint: str,
        owner_key: str,
    ) -> RequestReplayRow | None:
        existing = self._find_replay(skill_id, session_id, message_id)
        if existing is None:
            return None
        return self._verify_replay(existing, request_fingerprint, owner_key)

    def record_request(
        self,
        skill_id: str,
        session_id: str,
        message_id: str,
        request_fingerprint: str,
        owner_key: str,
        game_id: str | None = None,
    ) -> tuple[RequestReplayRow, bool]:
        """Claim the replay key and report whether this call is the one that claimed it.

        `created is False` means the request was already seen: the caller must
        replay or resume it instead of applying it again. A matching key with a
        different fingerprint is rejected without touching the game.
        """
        existing = self._find_replay(skill_id, session_id, message_id)
        if existing is not None:
            return self._verify_replay(existing, request_fingerprint, owner_key), False

        row = RequestReplayRow(
            skill_id=skill_id,
            session_id=session_id,
            message_id=message_id,
            request_fingerprint=request_fingerprint,
            owner_key=owner_key,
            game_id=game_id,
        )
        try:
            # A savepoint keeps a lost insert race from discarding the caller's transaction.
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            # A concurrent delivery of the same request won the unique constraint.
            # The re-read must lock: a plain SELECT would reuse this transaction's
            # REPEATABLE READ snapshot, which predates the rival commit.
            concurrent = self._find_replay(skill_id, session_id, message_id, for_update=True)
            if concurrent is None:
                raise
            return self._verify_replay(concurrent, request_fingerprint, owner_key), False
        return row, True

    def purge_request_replays(self, now: datetime, retention_days: int) -> int:
        """Delete replay responses after their retry value has expired."""
        cutoff = now - timedelta(days=retention_days)
        removed = self._session.query(RequestReplayRow).filter(RequestReplayRow.created_at < cutoff).delete()
        self._session.flush()
        return removed

    def store_response(self, replay: RequestReplayRow, response_payload: str, game_id: str | None = None) -> None:
        replay.response_payload = response_payload
        if game_id is not None:
            replay.game_id = game_id
        self._session.flush()

    def store_alice_response(
        self,
        replay: RequestReplayRow,
        response_payload: str,
        game_id: str | None = None,
    ) -> None:
        replay.alice_response_payload = response_payload
        if game_id is not None:
            replay.game_id = game_id
        self._session.flush()

    def _find_replay(
        self,
        skill_id: str,
        session_id: str,
        message_id: str,
        for_update: bool = False,
    ) -> RequestReplayRow | None:
        statement = select(RequestReplayRow).where(
            RequestReplayRow.skill_id == skill_id,
            RequestReplayRow.session_id == session_id,
            RequestReplayRow.message_id == message_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalars(statement).one_or_none()

    @staticmethod
    def _verify_replay(row: RequestReplayRow, request_fingerprint: str, owner_key: str) -> RequestReplayRow:
        if row.request_fingerprint != request_fingerprint or row.owner_key != owner_key:
            raise ReplayFingerprintConflictError(
                f"replay key ({row.skill_id}, {row.session_id}, {row.message_id}) reused with different request"
            )
        return row

    def _load_row(self, game_id: str, owner_key: str, expected_revision: int | None = None) -> GameRow:
        statement = select(GameRow).where(GameRow.id == game_id, GameRow.owner_key == owner_key)
        if expected_revision is not None:
            # A locking read serialises concurrent writers on the same game: the
            # second one waits, then sees the bumped revision and is rejected.
            # `populate_existing` makes that independent of the identity map: a
            # caller that still holds this row would otherwise be handed back the
            # revision and `moves` it read before taking the lock, and the guard
            # would compare a stale value against itself.
            statement = statement.with_for_update().execution_options(populate_existing=True)
        row = self._session.scalars(statement).one_or_none()
        if row is None:
            raise GameNotFoundError(f"game {game_id} is not available for this owner")
        if expected_revision is not None and row.revision != expected_revision:
            raise RevisionConflictError(f"game {game_id} is at revision {row.revision}, expected {expected_revision}")
        return row

    def _bump_revision(self, row: GameRow) -> GameState:
        row.revision += 1
        self._session.flush()
        return _to_state(row)

    @staticmethod
    def _actor_for_ply(row: GameRow, ply: int) -> MoveActor:
        starting_turn = chess.Board(row.initial_fen).turn
        moving_side = starting_turn if ply % 2 == 0 else not starting_turn
        return MoveActor.PLAYER if moving_side == PlayerColor(row.player_color).to_chess() else MoveActor.ENGINE

    @staticmethod
    def _move_row(row: GameRow, ply: int, uci: str, actor: MoveActor) -> GameMoveRow:
        moved_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        if actor is MoveActor.PLAYER:
            row.last_player_move_at = moved_at
        return GameMoveRow(
            game_id=row.id,
            ply=ply,
            uci=uci,
            actor=actor.value,
            created_at=moved_at,
        )
