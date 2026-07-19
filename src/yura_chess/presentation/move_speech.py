"""Naming pieces, squares and moves out loud.

Display text keeps plain algebraic squares (`e2`), the way the illegal-move
explanations already write them. Speech cannot: read aloud, `e2` is heard as a
word, so the pronunciation spells the file and the rank («е два»). The spellings
below are the exact inverse of the normaliser's file map, so anything the skill
says can be said back to it and still parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import chess

from yura_chess.domain.preferences import NotationStyle, PauseStyle

SQUARE_PATTERN = re.compile(r"\b([a-h])([1-8])\b")

# Alice reads `sil <[N]>` as a pause of N milliseconds. Pauses are only ever
# added: the skill cannot change how fast Alice speaks, so «быстрее» removes the
# pauses added here and nothing else.
PAUSE_MARKUP = " sil <[400]>"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

_FILE_SOUNDS: dict[str, str] = {
    "a": "а",
    "b": "бэ",
    "c": "цэ",
    "d": "дэ",
    "e": "е",
    "f": "эф",
    "g": "жэ",
    "h": "аш",
}

_RANK_SOUNDS: dict[str, str] = {
    "1": "один",
    "2": "два",
    "3": "три",
    "4": "четыре",
    "5": "пять",
    "6": "шесть",
    "7": "семь",
    "8": "восемь",
}

PIECE_NAMES: dict[int, str] = {
    chess.PAWN: "пешка",
    chess.KNIGHT: "конь",
    chess.BISHOP: "слон",
    chess.ROOK: "ладья",
    chess.QUEEN: "ферзь",
    chess.KING: "король",
}

PIECE_NAMES_ACCUSATIVE: dict[int, str] = {
    chess.PAWN: "пешку",
    chess.KNIGHT: "коня",
    chess.BISHOP: "слона",
    chess.ROOK: "ладью",
    chess.QUEEN: "ферзя",
    chess.KING: "короля",
}

PIECE_NAMES_PLURAL: dict[int, str] = {
    chess.PAWN: "пешки",
    chess.KNIGHT: "кони",
    chess.BISHOP: "слоны",
    chess.ROOK: "ладьи",
    chess.QUEEN: "ферзи",
    chess.KING: "короли",
}

# Plural forms only: they attach to any piece name without gender agreement.
COLOUR_PLURAL: dict[bool, str] = {chess.WHITE: "белые", chess.BLACK: "черные"}
COLOUR_GENITIVE: dict[bool, str] = {chess.WHITE: "белых", chess.BLACK: "черных"}


@dataclass(frozen=True, slots=True)
class Speech:
    """What to show and, only when it differs, how to pronounce it.

    Alice does the speaking; this layer only decides the two strings.
    """

    text: str
    tts: str | None = None

    @classmethod
    def of(cls, text: str) -> Speech:
        pronunciation = spell_squares(text)
        return cls(text, pronunciation if pronunciation != text else None)

    def spoken(self) -> str:
        return self.tts if self.tts is not None else self.text


def spell_square(name: str) -> str:
    """`e2` → «е два»."""
    return f"{_FILE_SOUNDS[name[0]]} {_RANK_SOUNDS[name[1]]}"


def spell_squares(text: str) -> str:
    """Replace every algebraic square in `text` with its pronunciation."""
    return SQUARE_PATTERN.sub(lambda match: spell_square(match.group(0)), text)


def spell_slowly(name: str) -> Speech:
    """A deliberate repeat of one coordinate; reads the board, never changes it."""
    file_name, rank = name[0], name[1]
    return Speech(
        text=f"Поле {name}: вертикаль {file_name}, горизонталь {rank}.",
        tts=(f"Поле {spell_square(name)}. Вертикаль — {_FILE_SOUNDS[file_name]}. Горизонталь — {_RANK_SOUNDS[rank]}."),
    )


def add_pauses(speech: Speech, style: PauseStyle) -> Speech:
    """Space out the pronunciation without touching the words or the display text."""
    if style is not PauseStyle.EXTENDED:
        return speech
    spoken = _SENTENCE_END.sub(PAUSE_MARKUP + " ", speech.spoken())
    return Speech(text=speech.text, tts=spoken)


def describe_move(
    board_before: chess.Board,
    move: chess.Move,
    notation: NotationStyle = NotationStyle.FULL,
) -> Speech:
    """Describe `move` in the position it is played in."""
    board_after = board_before.copy(stack=False)
    board_after.push(move)
    return Speech.of(_body(board_before, move, notation) + _suffix(board_after))


def describe_played_move(
    board_after: chess.Board,
    move: chess.Move,
    notation: NotationStyle = NotationStyle.FULL,
) -> Speech:
    """Describe a move when only the resulting position is available.

    The piece on the destination is still readable, but what it captured, and
    whether the move was a castling or a promotion, are not: this is the plain
    fallback, used only when the previous position was not kept.
    """
    piece = board_after.piece_at(move.to_square)
    name = PIECE_NAMES[piece.piece_type] if piece else "фигура"
    source, destination = chess.square_name(move.from_square), chess.square_name(move.to_square)
    squares = destination if notation is NotationStyle.SHORT else f"{source} {destination}"
    return Speech.of(f"{name} {squares}." + _suffix(board_after))


def _body(board: chess.Board, move: chess.Move, notation: NotationStyle = NotationStyle.FULL) -> str:
    if board.is_kingside_castling(move):
        return "Короткая рокировка."
    if board.is_queenside_castling(move):
        return "Длинная рокировка."

    piece = board.piece_at(move.from_square)
    name = PIECE_NAMES[piece.piece_type] if piece else "фигура"
    source, destination = chess.square_name(move.from_square), chess.square_name(move.to_square)
    # The short style names only where the piece lands; which piece moved and
    # what it did there is unchanged.
    origin = "" if notation is NotationStyle.SHORT else f" {source}"

    if board.is_en_passant(move):
        return f"{name}{origin} берет пешку на {destination} на проходе."
    captured = board.piece_at(move.to_square)
    if captured is not None:
        body = f"{name}{origin} берет {PIECE_NAMES_ACCUSATIVE[captured.piece_type]} на {destination}"
    else:
        body = f"{name}{origin} {destination}"
    if move.promotion is not None:
        body += f" и превращается в {PIECE_NAMES_ACCUSATIVE[move.promotion]}"
    return body + "."


def _suffix(board_after: chess.Board) -> str:
    if board_after.is_checkmate():
        return " Мат."
    if board_after.is_stalemate():
        return " Пат."
    if board_after.is_check():
        return " Шах."
    return ""
