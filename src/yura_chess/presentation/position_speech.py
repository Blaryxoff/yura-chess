"""Answer questions about the position without touching the game.

Four questions are supported: what stands on a square, where a kind of piece
stands, what one side has, and the whole board. The whole board is too long for
one reply, so it is read in stable groups of two ranks and continued on
«дальше» — the same page always contains the same ranks, which is what makes a
spoken board followable.

The sub-question is read off the normaliser's signature rather than parsed
again, so the piece, file and rank vocabulary has exactly one definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import chess

from yura_chess.presentation.move_speech import (
    COLOUR_GENITIVE,
    COLOUR_PLURAL,
    PIECE_NAMES,
    PIECE_NAMES_PLURAL,
    Speech,
    describe_move,
    spell_slowly,
)
from yura_chess.voice.normalizer import normalize
from yura_chess.voice.types import Normalized, TokenKind

# Two ranks per group: short enough to hold in the ear, and 8 ranks divide evenly.
RANKS_PER_PAGE = 2
PAGE_COUNT = 8 // RANKS_PER_PAGE

_PIECE_LETTERS: dict[str, int] = {
    "P": chess.PAWN,
    "N": chess.KNIGHT,
    "B": chess.BISHOP,
    "R": chess.ROOK,
    "Q": chess.QUEEN,
    "K": chess.KING,
}

_RANK_ORDINALS: dict[int, str] = {
    1: "первая",
    2: "вторая",
    3: "третья",
    4: "четвертая",
    5: "пятая",
    6: "шестая",
    7: "седьмая",
    8: "восьмая",
}

_WHITE_WORD = re.compile(r"^бел")
_BLACK_WORD = re.compile(r"^черн")
_NEXT_PAGE = re.compile(r"\b(дальше|далее|еще|дальнейш)")
_SLOWLY = re.compile(r"медленн|по буквам|по слогам|повтори координат")
_LAST_MOVE = re.compile(r"последн(ий|его) ход|как (ты|я) походил")
_TURN = re.compile(r"чей ход|кто ходит|кому ходить|моя очередь")
_CHECK = re.compile(r"есть ли шах|кто под шахом|шах сейчас")

_CONTINUATION = " Скажите «дальше», чтобы продолжить."


class PositionQuery(StrEnum):
    SQUARE = "square"
    PIECE_KIND = "piece_kind"
    SIDE = "side"
    WHOLE_BOARD = "whole_board"
    SLOW_SQUARE = "slow_square"
    LAST_MOVE = "last_move"
    TURN = "turn"
    CHECK = "check"


@dataclass(frozen=True, slots=True)
class PositionAnswer:
    query: PositionQuery
    speech: Speech
    # Which group of the whole board was read, and whether another one follows.
    page: int = 0
    has_next: bool = False


def answer_position_query(utterance: str, board: chess.Board, page: int = 0) -> PositionAnswer:
    """Answer whatever `utterance` asks about `board`; never mutates the board."""
    normalized = normalize(utterance)
    square = _first_square(normalized)
    colour = _colour(normalized)
    piece_type = _piece_type(normalized)

    if _LAST_MOVE.search(normalized.text):
        return PositionAnswer(PositionQuery.LAST_MOVE, describe_last_move(board))
    if _TURN.search(normalized.text):
        side = "белых" if board.turn == chess.WHITE else "черных"
        return PositionAnswer(PositionQuery.TURN, Speech.of(f"Сейчас ход {side}."))
    if _CHECK.search(normalized.text):
        if not board.is_check():
            return PositionAnswer(PositionQuery.CHECK, Speech.of("Сейчас шаха нет."))
        side = "белому" if board.turn == chess.WHITE else "черному"
        return PositionAnswer(PositionQuery.CHECK, Speech.of(f"Шах {side} королю."))
    if square is not None and _SLOWLY.search(normalized.text):
        return PositionAnswer(PositionQuery.SLOW_SQUARE, spell_slowly(square))
    if square is not None and piece_type is None:
        return PositionAnswer(PositionQuery.SQUARE, describe_square(board, square))
    if piece_type is not None:
        return PositionAnswer(PositionQuery.PIECE_KIND, describe_piece_kind(board, piece_type, colour))
    if colour is not None:
        return PositionAnswer(PositionQuery.SIDE, describe_side(board, colour))

    if _NEXT_PAGE.search(normalized.text):
        page += 1
    return read_board(board, page)


def describe_last_move(board: chess.Board) -> Speech:
    if not board.move_stack:
        return Speech.of("Ходов еще не было.")
    before = board.copy(stack=True)
    move = before.pop()
    return Speech.of(f"Последний ход: {describe_move(before, move).text}")


def describe_square(board: chess.Board, square: str) -> Speech:
    piece = board.piece_at(chess.parse_square(square))
    if piece is None:
        return Speech.of(f"Поле {square} пустое.")
    return Speech.of(f"На {square} — {PIECE_NAMES[piece.piece_type]} {COLOUR_GENITIVE[piece.color]}.")


def describe_piece_kind(board: chess.Board, piece_type: int, colour: chess.Color | None = None) -> Speech:
    """Where one kind of piece stands, for one side or for both."""
    colours = (colour,) if colour is not None else (chess.WHITE, chess.BLACK)
    parts = []
    for side in colours:
        squares = _squares_of(board, piece_type, side)
        name = PIECE_NAMES_PLURAL[piece_type]
        if squares:
            parts.append(f"{COLOUR_PLURAL[side]} {name}: {', '.join(squares)}")
        else:
            parts.append(f"{name} {COLOUR_GENITIVE[side]} нет")
    listing = "; ".join(parts)
    return Speech.of(listing[0].upper() + listing[1:] + ".")


def describe_side(board: chess.Board, colour: chess.Color) -> Speech:
    listing = _side_listing(board, colour)
    if not listing:
        return Speech.of(f"У {COLOUR_GENITIVE[colour]} фигур нет.")
    return Speech.of(f"У {COLOUR_GENITIVE[colour]}: {listing}.")


def read_board(board: chess.Board, page: int = 0) -> PositionAnswer:
    """One stable group of ranks, read from the eighth rank down."""
    page = max(0, min(page, PAGE_COUNT - 1))
    top_rank = 8 - page * RANKS_PER_PAGE
    lines = [_rank_line(board, rank) for rank in range(top_rank, top_rank - RANKS_PER_PAGE, -1)]
    has_next = page + 1 < PAGE_COUNT
    text = " ".join(lines) + (_CONTINUATION if has_next else "")
    return PositionAnswer(PositionQuery.WHOLE_BOARD, Speech.of(text), page=page, has_next=has_next)


def _rank_line(board: chess.Board, rank: int) -> str:
    ordinal = _RANK_ORDINALS[rank].capitalize()
    parts = []
    for colour in (chess.WHITE, chess.BLACK):
        squares = [
            f"{PIECE_NAMES[piece.piece_type]} {chess.square_name(square)}"
            for square in chess.SquareSet(chess.BB_RANKS[rank - 1])
            if (piece := board.piece_at(square)) is not None and piece.color == colour
        ]
        if squares:
            parts.append(f"{COLOUR_PLURAL[colour]} — {', '.join(squares)}")
    if not parts:
        return f"{ordinal} горизонталь пуста."
    return f"{ordinal} горизонталь: {'; '.join(parts)}."


def _side_listing(board: chess.Board, colour: chess.Color) -> str:
    parts = []
    for piece_type in (chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN):
        squares = _squares_of(board, piece_type, colour)
        if not squares:
            continue
        name = PIECE_NAMES[piece_type] if len(squares) == 1 else PIECE_NAMES_PLURAL[piece_type]
        parts.append(f"{name} {', '.join(squares)}")
    return "; ".join(parts)


def _squares_of(board: chess.Board, piece_type: int, colour: chess.Color) -> list[str]:
    return [chess.square_name(square) for square in sorted(board.pieces(piece_type, colour))]


def _first_square(normalized: Normalized) -> str | None:
    for token in normalized.signature:
        if token.kind is TokenKind.SQUARE:
            return token.value
    return None


def _piece_type(normalized: Normalized) -> int | None:
    for token in normalized.signature:
        if token.kind is TokenKind.PIECE:
            return _PIECE_LETTERS[token.value]
    return None


def _colour(normalized: Normalized) -> chess.Color | None:
    for word in normalized.words:
        if _WHITE_WORD.match(word):
            return chess.WHITE
        if _BLACK_WORD.match(word):
            return chess.BLACK
    return None
