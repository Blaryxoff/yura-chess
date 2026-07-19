"""Name the opening and the stage of the game.

Both answers are read-only: they look at the canonical history and the current
material, never at the engine, and never touch the board they are given. An
unrecognised line is answered honestly rather than guessed at — the shipped ECO
set is compact, so «дебют не определён» is a normal answer, not a failure.

The opening set is the offline CC0 import in `yura_chess/data/openings.tsv`;
runtime never reaches for the source repository.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib.resources import files

import chess

from yura_chess.presentation.move_speech import Speech

_OPENINGS_RESOURCE = ("yura_chess", "data", "openings.tsv")

# Speelman's threshold: the endgame has started once neither side has more than
# thirteen points of material besides pawns and the king.
_ENDGAME_MATERIAL = 13
_PIECE_VALUES: dict[int, int] = {
    chess.PAWN: 0,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

# The opening lasts while the pieces are still coming out: ten full moves at
# most, and only until six of the eight minor pieces have left home.
_OPENING_PLIES = 20
_UNDEVELOPED_MINORS = 3
_MINOR_HOME_SQUARES: tuple[tuple[int, chess.Color, int], ...] = (
    (chess.B1, chess.WHITE, chess.KNIGHT),
    (chess.G1, chess.WHITE, chess.KNIGHT),
    (chess.C1, chess.WHITE, chess.BISHOP),
    (chess.F1, chess.WHITE, chess.BISHOP),
    (chess.B8, chess.BLACK, chess.KNIGHT),
    (chess.G8, chess.BLACK, chess.KNIGHT),
    (chess.C8, chess.BLACK, chess.BISHOP),
    (chess.F8, chess.BLACK, chess.BISHOP),
)


class GameStage(StrEnum):
    OPENING = "opening"
    MIDDLEGAME = "middlegame"
    ENDGAME = "endgame"


@dataclass(frozen=True, slots=True)
class OpeningName:
    eco: str
    opening: str
    variation: str

    @property
    def full_name(self) -> str:
        return f"{self.opening}: {self.variation}" if self.variation else self.opening


_STAGE_NAMES: dict[GameStage, str] = {
    GameStage.OPENING: "дебют",
    GameStage.MIDDLEGAME: "миттельшпиль",
    GameStage.ENDGAME: "эндшпиль",
}


@cache
def _opening_index() -> dict[tuple[str, ...], OpeningName]:
    """UCI prefix → opening, loaded once from the packaged import."""
    resource = files(_OPENINGS_RESOURCE[0]).joinpath(*_OPENINGS_RESOURCE[1:])
    reader = csv.DictReader(resource.read_text(encoding="utf-8").splitlines(), delimiter="\t")
    return {tuple(row["uci"].split()): OpeningName(row["eco"], row["opening"], row["variation"]) for row in reader}


def identify_opening(board: chess.Board) -> OpeningName | None:
    """The longest known ECO line the game still starts with, if there is one."""
    if board.root() != chess.Board():
        return None
    moves = tuple(move.uci() for move in board.move_stack)
    index = _opening_index()
    for length in range(len(moves), 0, -1):
        known = index.get(moves[:length])
        if known is not None:
            return known
    return None


def game_stage(board: chess.Board) -> GameStage:
    """Which stage the position is in, by material first and development second.

    Material decides the endgame on its own: a position traded down to rooks and
    a minor piece is an endgame however early it happened.
    """
    if all(_material(board, colour) <= _ENDGAME_MATERIAL for colour in chess.COLORS):
        return GameStage.ENDGAME
    if len(board.move_stack) < _OPENING_PLIES and _undeveloped_minors(board) >= _UNDEVELOPED_MINORS:
        return GameStage.OPENING
    return GameStage.MIDDLEGAME


def describe_opening(board: chess.Board) -> Speech:
    known = identify_opening(board)
    if known is None:
        return Speech.of("Дебют не определён.")
    return Speech.of(f"Это {known.full_name}, код {known.eco}.")


def describe_stage(board: chess.Board) -> Speech:
    return Speech.of(f"Сейчас {_STAGE_NAMES[game_stage(board)]}.")


def _material(board: chess.Board, colour: chess.Color) -> int:
    return sum(_PIECE_VALUES[piece_type] * len(board.pieces(piece_type, colour)) for piece_type in chess.PIECE_TYPES)


def _undeveloped_minors(board: chess.Board) -> int:
    return sum(
        1
        for square, colour, piece_type in _MINOR_HOME_SQUARES
        if board.piece_at(square) == chess.Piece(piece_type, colour)
    )
