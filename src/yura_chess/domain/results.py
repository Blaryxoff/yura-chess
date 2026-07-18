"""Outcome detection and the transport-neutral result of one turn.

Automatic ends are decided by the rules alone; the two claimable draws are only
reported here and require an explicit player command.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import chess

from yura_chess.domain.game import GameState, GameStatus, PlayerColor


class GameEnd(StrEnum):
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    INSUFFICIENT_MATERIAL = "insufficient_material"
    SEVENTY_FIVE_MOVES = "seventy_five_moves"
    FIVEFOLD_REPETITION = "fivefold_repetition"
    FIFTY_MOVES = "fifty_moves"
    THREEFOLD_REPETITION = "threefold_repetition"
    RESIGNATION = "resignation"


class TurnStatus(StrEnum):
    OK = "ok"
    GAME_OVER = "game_over"
    ILLEGAL_MOVE = "illegal_move"
    NOT_PLAYER_TURN = "not_player_turn"
    GAME_ALREADY_FINISHED = "game_already_finished"
    DRAW_NOT_CLAIMABLE = "draw_not_claimable"
    UNDO_REJECTED = "undo_rejected"
    # The player's move is safely stored; only the engine reply is still owed.
    ENGINE_UNAVAILABLE = "engine_unavailable"


@dataclass(frozen=True, slots=True)
class GameOutcome:
    end: GameEnd
    winner: PlayerColor | None = None


def automatic_outcome(board: chess.Board) -> GameOutcome | None:
    """Ends that apply without any claim; never consults the claimable draws."""
    if board.is_checkmate():
        loser = PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK
        winner = PlayerColor.BLACK if loser is PlayerColor.WHITE else PlayerColor.WHITE
        return GameOutcome(GameEnd.CHECKMATE, winner)
    if board.is_stalemate():
        return GameOutcome(GameEnd.STALEMATE)
    if board.is_insufficient_material():
        return GameOutcome(GameEnd.INSUFFICIENT_MATERIAL)
    if board.is_seventyfive_moves():
        return GameOutcome(GameEnd.SEVENTY_FIVE_MOVES)
    if board.is_fivefold_repetition():
        return GameOutcome(GameEnd.FIVEFOLD_REPETITION)
    return None


def claimable_draw(board: chess.Board) -> GameEnd | None:
    """The draws a player may demand, reported only on an explicit command."""
    if board.can_claim_fifty_moves():
        return GameEnd.FIFTY_MOVES
    if board.can_claim_threefold_repetition():
        return GameEnd.THREEFOLD_REPETITION
    return None


@dataclass(frozen=True, slots=True)
class TurnResult:
    """What one request did to the game, independent of any voice protocol."""

    status: TurnStatus
    game_id: str
    revision: int
    fen: str
    moves: tuple[str, ...]
    player_color: PlayerColor
    game_status: GameStatus
    player_move: str | None = None
    engine_move: str | None = None
    outcome: GameOutcome | None = None
    detail: str | None = None
    replayed: bool = False

    @classmethod
    def from_state(
        cls,
        state: GameState,
        status: TurnStatus,
        board: chess.Board | None = None,
        player_move: str | None = None,
        engine_move: str | None = None,
        outcome: GameOutcome | None = None,
        detail: str | None = None,
    ) -> TurnResult:
        return cls(
            status=status,
            game_id=state.id,
            revision=state.revision,
            fen=(board or state.board()).fen(),
            moves=state.moves,
            player_color=state.player_color,
            game_status=state.status,
            player_move=player_move,
            engine_move=engine_move,
            outcome=outcome,
            detail=detail,
        )

    def to_payload(self) -> str:
        body: dict[str, Any] = {
            "status": self.status.value,
            "game_id": self.game_id,
            "revision": self.revision,
            "fen": self.fen,
            "moves": list(self.moves),
            "player_color": self.player_color.value,
            "game_status": self.game_status.value,
            "player_move": self.player_move,
            "engine_move": self.engine_move,
            "detail": self.detail,
            "outcome": (
                {"end": self.outcome.end.value, "winner": self.outcome.winner.value if self.outcome.winner else None}
                if self.outcome
                else None
            ),
        }
        return json.dumps(body, ensure_ascii=False)

    @classmethod
    def from_payload(cls, payload: str) -> TurnResult:
        """Rebuild a stored answer; a replay is always marked as one."""
        body = json.loads(payload)
        outcome = body.get("outcome")
        return cls(
            status=TurnStatus(body["status"]),
            game_id=body["game_id"],
            revision=body["revision"],
            fen=body["fen"],
            moves=tuple(body["moves"]),
            player_color=PlayerColor(body["player_color"]),
            game_status=GameStatus(body["game_status"]),
            player_move=body["player_move"],
            engine_move=body["engine_move"],
            outcome=(
                GameOutcome(
                    GameEnd(outcome["end"]),
                    PlayerColor(outcome["winner"]) if outcome["winner"] else None,
                )
                if outcome
                else None
            ),
            detail=body["detail"],
            replayed=True,
        )
