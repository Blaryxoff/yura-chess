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
from yura_chess.presentation.russian import plural_form
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
_LAST_MOVE = re.compile(
    r"последн(ий|его) ход|как (ты|я) походил|какой (ты )?ход (сделал|сделала|сыграл|сыграла)|"
    r"повтори (свой|последний свой|предыдущий свой) ход|твой последний ход"
)
_NUMBER_WORDS = {
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
}
_ORDINALS = {
    1: "Первый",
    2: "Второй",
    3: "Третий",
    4: "Четвертый",
    5: "Пятый",
    6: "Шестой",
    7: "Седьмой",
    8: "Восьмой",
    9: "Девятый",
    10: "Десятый",
}
_HISTORY_AGO = re.compile(
    rf"\b(?P<count>\d+|{'|'.join(sorted(_NUMBER_WORDS, key=len, reverse=True))})\s+"
    r"(?:ход(?:а|ов)?|раз(?:а)?)\s+назад\b"
)

# Masculine ordinals agree with «ход», feminine ones with «горизонталь». They are
# deliberately kept out of the normaliser's rank map: a RANK token emitted for
# «на седьмой» could merge into a square and silently change which move is played.
_MOVE_ORDINALS: dict[str, int] = {
    "первый": 1,
    "первого": 1,
    "второй": 2,
    "второго": 2,
    "третий": 3,
    "третьего": 3,
    "четвертый": 4,
    "четвертого": 4,
    "пятый": 5,
    "пятого": 5,
    "шестой": 6,
    "шестого": 6,
    "седьмой": 7,
    "седьмого": 7,
    "восьмой": 8,
    "восьмого": 8,
    "девятый": 9,
    "девятого": 9,
    "десятый": 10,
    "десятого": 10,
}
_RANK_ORDINAL_WORDS: dict[str, int] = {
    "первая": 1,
    "первой": 1,
    "первую": 1,
    "вторая": 2,
    "второй": 2,
    "вторую": 2,
    "третья": 3,
    "третьей": 3,
    "третью": 3,
    "четвертая": 4,
    "четвертой": 4,
    "четвертую": 4,
    "пятая": 5,
    "пятой": 5,
    "пятую": 5,
    "шестая": 6,
    "шестой": 6,
    "шестую": 6,
    "седьмая": 7,
    "седьмой": 7,
    "седьмую": 7,
    "восьмая": 8,
    "восьмой": 8,
    "восьмую": 8,
}
# «горизонталь семь» names the rank by its cardinal number instead.
_RANK_NUMERALS: dict[str, int] = {
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
}
_NUMBERED_MOVE = re.compile(
    rf"\b(?P<index>\d+|{'|'.join(sorted(_MOVE_ORDINALS, key=len, reverse=True))})\s+(?P<full>полн\w+\s+)?ход\b"
    rf"|\b(?P<full_after>полн\w+\s+)?ход\w*\s+номер\s+"
    rf"(?P<numbered>\d+|{'|'.join(sorted(_NUMBER_WORDS, key=len, reverse=True))})\b"
)
# Public so the router can key a board question on the same grammar the reader
# understands: a bare «горизонталь» is a rules word, not a request to read one.
RANK_LINE = re.compile(
    rf"\b(?P<rank>[1-8]|{'|'.join(sorted(_RANK_ORDINAL_WORDS, key=len, reverse=True))})\s+горизонтал"
    rf"|\bгоризонтал\w*\s+(?:номер\s+)?(?P<after>[1-8]|{'|'.join(sorted(_RANK_NUMERALS, key=len, reverse=True))})\b"
)
_MOVES = ("ход", "хода", "ходов")
_FULL_MOVES = ("полный ход", "полных хода", "полных ходов")
_TURN = re.compile(r"чей ход|кто ходит|кому ходить|моя очередь")
_CHECK = re.compile(r"есть ли шах|кто под шахом|шах сейчас")

_CONTINUATION = " Скажите «дальше», чтобы продолжить."


class PositionQuery(StrEnum):
    SQUARE = "square"
    PIECE_KIND = "piece_kind"
    SIDE = "side"
    WHOLE_BOARD = "whole_board"
    RANK = "rank"
    SLOW_SQUARE = "slow_square"
    LAST_MOVE = "last_move"
    HISTORY = "history"
    # A ply named by its number from the start, as opposed to `HISTORY`'s countback.
    NUMBERED_MOVE = "numbered_move"
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

    history_count = _history_count(normalized.text)
    if history_count is not None:
        return PositionAnswer(PositionQuery.HISTORY, describe_historical_move(board, history_count, colour))
    # Before the piece and colour branches: «какой был второй ход черных» names a
    # side, and «на седьмой горизонтали» a rank, but neither asks where a kind of
    # piece stands.
    rank = _rank_asked(normalized.text)
    if rank is not None:
        return PositionAnswer(PositionQuery.RANK, Speech.of(_rank_line(board, rank)))
    numbered = _numbered_move(normalized.text)
    if numbered is not None:
        # «второй полный ход» counts pairs of plies; «второй ход черных» already
        # names one side, so there the colour filter gives the same ply anyway.
        index, full = numbered
        speech = (
            describe_full_move(board, index)
            if full and colour is None
            else describe_numbered_move(board, index, colour)
        )
        return PositionAnswer(PositionQuery.NUMBERED_MOVE, speech)
    if _LAST_MOVE.search(normalized.text):
        if colour is not None:
            return PositionAnswer(PositionQuery.HISTORY, describe_historical_move(board, 1, colour))
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


def describe_historical_move(board: chess.Board, count: int, colour: chess.Color | None = None) -> Speech:
    """Describe the Nth previous ply, optionally counting only one colour."""
    history = _move_history(board)
    if colour is not None:
        history = [item for item in history if item[2] == colour]
    if count < 1 or count > len(history):
        return _no_such_move(len(history), f" у {COLOUR_GENITIVE[colour]}" if colour is not None else "")

    before, move, _ = history[-count]
    ordinal = _ORDINALS.get(count, f"Предыдущий ход номер {count}")
    side = f" {COLOUR_GENITIVE[colour]}" if colour is not None else " в партии"
    return Speech.of(f"{ordinal} предыдущий ход{side}: {describe_move(before, move).text}")


def _no_such_move(played: int, side: str) -> Speech:
    """Say that the move asked about was never played, counting in Russian."""
    if played == 0:
        return Speech.of(f"Ходов{side} еще не было.")
    if played == 1:
        return Speech.of(f"Не могу найти такой ход: в партии{side} был только один ход.")
    return Speech.of(f"Не могу найти такой ход: в партии{side} было только {played} {plural_form(played, _MOVES)}.")


def describe_numbered_move(board: chess.Board, index: int, colour: chess.Color | None = None) -> Speech:
    """Describe the Nth ply counted from the start, optionally for one colour only."""
    history = _move_history(board)
    if colour is not None:
        history = [item for item in history if item[2] == colour]
    side = f" {COLOUR_GENITIVE[colour]}" if colour is not None else ""
    if index < 1 or index > len(history):
        return _no_such_move(len(history), f" у {COLOUR_GENITIVE[colour]}" if colour is not None else "")

    before, move, _ = history[index - 1]
    ordinal = _ORDINALS.get(index, f"{index}-й")
    return Speech.of(f"{ordinal} ход{side}: {describe_move(before, move).text}")


def describe_full_move(board: chess.Board, index: int) -> Speech:
    """Describe both halves of the numbered full move, the way a scoresheet lists it."""
    history = _move_history(board)
    # The half a pair the game has reached is still answerable, but only the
    # finished pairs are counted, the way «сколько ходов сыграно» counts them.
    played = len(history) // 2
    if index < 1 or index > (len(history) + 1) // 2:
        if not history:
            return Speech.of("Ходов еще не было.")
        if played == 0:
            return Speech.of("Полных ходов еще не было.")
        if played == 1:
            return Speech.of("Не могу найти такой ход: в партии был только один полный ход.")
        return Speech.of(f"Не могу найти такой ход: в партии было только {played} {plural_form(played, _FULL_MOVES)}.")

    ordinal = _ORDINALS.get(index, f"{index}-й")
    pair = history[2 * (index - 1) : 2 * index]
    if len(pair) == 1:
        before, move, colour = pair[0]
        # Half a pair is not a full move yet, so it is named as the one side's.
        return Speech.of(f"{ordinal} ход {COLOUR_GENITIVE[colour]}: {describe_move(before, move).text}")

    parts = [
        f"{COLOUR_PLURAL[colour].capitalize()} — {describe_move(before, move).text}" for before, move, colour in pair
    ]
    return Speech.of(f"{ordinal} полный ход. {' '.join(parts)}")


def describe_recent_moves(board: chess.Board, count: int = 2) -> Speech:
    """Read the last individual actions in chronological order."""
    history = _move_history(board)
    if not history:
        return Speech.of("Ходов еще не было.")
    parts = [
        f"{COLOUR_PLURAL[colour].capitalize()} — {describe_move(before, move).text}"
        for before, move, colour in history[-count:]
    ]
    return Speech.of(" ".join(parts))


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


def _history_count(text: str) -> int | None:
    match = _HISTORY_AGO.search(text)
    if match is None:
        return None
    value = match.group("count")
    return int(value) if value.isdigit() else _NUMBER_WORDS[value]


def _numbered_move(text: str) -> tuple[int, bool] | None:
    """Read the move number asked about and whether it was named as a full move."""
    match = _NUMBERED_MOVE.search(text)
    if match is None:
        return None
    full = bool(match.group("full") or match.group("full_after"))
    value = match.group("index")
    if value is None:
        numbered = match.group("numbered")
        return (int(numbered) if numbered.isdigit() else _NUMBER_WORDS[numbered]), full
    return (int(value) if value.isdigit() else _MOVE_ORDINALS[value]), full


def _rank_asked(text: str) -> int | None:
    match = RANK_LINE.search(text)
    if match is None:
        return None
    value = match.group("rank")
    if value is None:
        after = match.group("after")
        return int(after) if after.isdigit() else _RANK_NUMERALS[after]
    return int(value) if value.isdigit() else _RANK_ORDINAL_WORDS[value]


def _move_history(board: chess.Board) -> list[tuple[chess.Board, chess.Move, chess.Color]]:
    moves = tuple(board.move_stack)
    replay = board.copy(stack=True)
    while replay.move_stack:
        replay.pop()
    history = []
    for move in moves:
        before = replay.copy(stack=False)
        colour = replay.turn
        replay.push(move)
        history.append((before, move, colour))
    return history
