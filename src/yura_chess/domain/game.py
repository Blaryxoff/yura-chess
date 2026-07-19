"""Canonical game model.

The full UCI move list is the single source of truth: every board is rebuilt by
replaying the moves from the starting FEN, never by loading a stored snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

import chess

START_FEN = chess.STARTING_FEN


class GameStatus(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"
    RESIGNED = "resigned"


class PlayerColor(StrEnum):
    WHITE = "white"
    BLACK = "black"

    def to_chess(self) -> chess.Color:
        return chess.WHITE if self is PlayerColor.WHITE else chess.BLACK


class MoveActor(StrEnum):
    PLAYER = "player"
    ENGINE = "engine"


class InvalidMoveHistoryError(ValueError):
    """A stored UCI history cannot be replayed from its starting FEN."""


@dataclass(frozen=True, slots=True)
class EngineSettings:
    skill_level: int = 5
    move_time_ms: int = 1000


@dataclass(frozen=True, slots=True)
class PendingEngineTurn:
    """A player move committed while the engine reply is still being computed."""

    token: str
    player_move_uci: str


@dataclass(frozen=True, slots=True)
class GameState:
    id: str
    owner_key: str
    status: GameStatus
    player_color: PlayerColor
    initial_fen: str
    moves: tuple[str, ...]
    revision: int
    engine: EngineSettings
    created_at: datetime
    updated_at: datetime
    last_player_move_at: datetime | None = None
    pending_engine_turn: PendingEngineTurn | None = None

    def board(self) -> chess.Board:
        """Rebuild the position by replaying the whole UCI history."""
        board = chess.Board(self.initial_fen)
        for ply, uci in enumerate(self.moves):
            try:
                move = chess.Move.from_uci(uci)
            except ValueError as error:
                raise InvalidMoveHistoryError(f"game {self.id}: ply {ply} is not valid UCI: {uci!r}") from error
            if move not in board.legal_moves:
                raise InvalidMoveHistoryError(f"game {self.id}: ply {ply} move {uci!r} is illegal in this position")
            board.push(move)
        return board

    def with_moves(self, *moves: str) -> GameState:
        return replace(self, moves=self.moves + moves)

    @property
    def is_player_to_move(self) -> bool:
        return self.board().turn == self.player_color.to_chess()
