"""Answer factual questions about the game itself, not about the position.

Everything here is derived from the canonical history the board was replayed
from, so the same game always produces the same answer after a reload. Nothing
in this module mutates the board: replays are done on copies.

The questions live in one pattern per fact, and `QUESTION_PATTERN` is the union
the command router matches on, so the router and the answers can never disagree
about what counts as a factual question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import chess

from yura_chess.presentation.move_speech import (
    COLOUR_GENITIVE,
    PIECE_NAMES,
    PIECE_NAMES_ACCUSATIVE,
    Speech,
)
from yura_chess.presentation.position_speech import describe_last_move
from yura_chess.voice.normalizer import normalize


class GameFact(StrEnum):
    COLOR = "color"
    MOVE_NUMBER = "move_number"
    MOVES_PLAYED = "moves_played"
    CAPTURED = "captured"
    CASTLING = "castling"
    CHECK_ATTACKERS = "check_attackers"
    LAST_MOVE_CHANGES = "last_move_changes"


@dataclass(frozen=True, slots=True)
class GameFactAnswer:
    fact: GameFact
    speech: Speech


# The check question is deliberately narrow: a bare «шах» must stay the plain
# «есть ли шах» position question rather than the attacker listing.
_PATTERNS: tuple[tuple[GameFact, re.Pattern[str]], ...] = (
    (
        GameFact.COLOR,
        re.compile(r"за кого я играю|каким цветом|какого цвета я|какой у меня цвет|мой цвет|я белые или черные"),
    ),
    (
        GameFact.CASTLING,
        re.compile(
            r"(могу|можно|возможна|доступна|осталась|есть)\w*( ли)?( я| мне| у меня| сейчас| еще)*"
            r"( сделать)? рокиров|прав\w* на рокировк|рокировка возможна"
        ),
    ),
    (
        GameFact.CHECK_ATTACKERS,
        re.compile(r"кто (дает|дал|объявил|поставил|ставит) шах|чем( мне| нам)? шах|как\w+ фигур\w* атаку|кто атаку"),
    ),
    (
        GameFact.CAPTURED,
        re.compile(
            r"как\w+ фигур\w* (съеден|снят|побит|сбит|потерян|взят)|"
            r"что (съедено|снято|побито|взято)|кого (я|ты) (съел|потерял|взял)|"
            r"сколько фигур|какие фигуры съел|съеденные фигуры|снятые фигуры"
        ),
    ),
    (
        GameFact.MOVES_PLAYED,
        re.compile(r"сколько (полных )?ходов|сколько мы сыграли|сколько (я|ты) (сделал|сыграл)"),
    ),
    (
        GameFact.MOVE_NUMBER,
        re.compile(r"номер хода|какой сейчас ход|какой ход по счету|который сейчас ход|на каком ходу"),
    ),
    (
        GameFact.LAST_MOVE_CHANGES,
        re.compile(r"что (изменил|поменял|дал)\w*( последний)?( ход)?|что изменилось|что поменялось"),
    ),
)

QUESTION_PATTERN = re.compile("|".join(pattern.pattern for _, pattern in _PATTERNS))

# Nominative, genitive singular and genitive plural: the three forms Russian
# needs after a numeral.
_MOVE_FORMS = ("ход", "хода", "ходов")
_PIECE_COUNT_FORMS: dict[int, tuple[str, str, str]] = {
    chess.PAWN: ("пешка", "пешки", "пешек"),
    chess.KNIGHT: ("конь", "коня", "коней"),
    chess.BISHOP: ("слон", "слона", "слонов"),
    chess.ROOK: ("ладья", "ладьи", "ладей"),
    chess.QUEEN: ("ферзь", "ферзя", "ферзей"),
    chess.KING: ("король", "короля", "королей"),
}
_COLOUR_INSTRUMENTAL: dict[bool, str] = {chess.WHITE: "белыми", chess.BLACK: "черными"}
_CAPTURE_ORDER = (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN)


def answer_game_fact(utterance: str, board: chess.Board, player: chess.Color) -> GameFactAnswer | None:
    """Answer a factual question about the game; `None` when it asks something else."""
    text = normalize(utterance).text
    for fact, pattern in _PATTERNS:
        if pattern.search(text):
            return GameFactAnswer(fact, _ANSWERS[fact](board, player))
    return None


def describe_color(board: chess.Board, player: chess.Color) -> Speech:
    turn = "ваш ход" if board.turn == player else "мой ход"
    return Speech.of(
        f"Вы играете {_COLOUR_INSTRUMENTAL[player]}, я — {_COLOUR_INSTRUMENTAL[not player]}. Сейчас {turn}."
    )


def describe_move_number(board: chess.Board, player: chess.Color) -> Speech:
    side = "ваш" if board.turn == player else "мой"
    return Speech.of(f"Сейчас {board.fullmove_number}-й ход, и он {side}.")


def describe_moves_played(board: chess.Board, player: chess.Color) -> Speech:
    plies = len(board.move_stack)
    if plies == 0:
        return Speech.of("Ходов еще не было.")
    full = plies // 2
    played = f"Сыграно {plies} {_plural(plies, _MOVE_FORMS)}"
    if full == 0:
        return Speech.of(f"{played}, ни одного полного хода еще нет.")
    full_forms = _plural(full, ("полный", "полных", "полных"))
    return Speech.of(f"{played}, это {full} {full_forms} {_plural(full, _MOVE_FORMS)}.")


def describe_captured(board: chess.Board, player: chess.Color) -> Speech:
    taken = _captured_pieces(board)
    mine, theirs = _piece_listing(taken[player]), _piece_listing(taken[not player])
    if not mine and not theirs:
        return Speech.of("Пока никто не снял ни одной фигуры.")
    yours = f"Вы взяли: {mine}." if mine else "Вы пока ничего не взяли."
    ours = f"Я взяла: {theirs}." if theirs else "Я пока ничего не взяла."
    return Speech.of(f"{yours} {ours}")


def describe_castling(board: chess.Board, player: chess.Color) -> Speech:
    short = _castling_state(board, player, chess.BB_H1 | chess.BB_H8)
    long = _castling_state(board, player, chess.BB_A1 | chess.BB_A8)
    return Speech.of(f"Короткая рокировка {short}. Длинная рокировка {long}.")


def describe_check_attackers(board: chess.Board, player: chess.Color) -> Speech:
    if not board.is_check():
        return Speech.of("Сейчас шаха нет.")
    king_square = board.king(board.turn)
    assert king_square is not None
    attackers = [
        f"{PIECE_NAMES[piece.piece_type]} {chess.square_name(square)}"
        for square in sorted(board.attackers(not board.turn, king_square))
        if (piece := board.piece_at(square)) is not None
    ]
    whose = "вашему" if board.turn == player else "моему"
    gives = "Шах дает" if len(attackers) == 1 else "Шах дают"
    return Speech.of(f"Шах {whose} королю на {chess.square_name(king_square)}. {gives}: {', '.join(attackers)}.")


def describe_last_move_changes(board: chess.Board, player: chess.Color) -> Speech:
    if not board.move_stack:
        return Speech.of("Ходов еще не было.")
    before = board.copy(stack=True)
    move = before.pop()
    source, destination = chess.square_name(move.from_square), chess.square_name(move.to_square)
    moved = board.piece_at(move.to_square)
    changes = [f"поле {source} освободилось"]
    if moved is not None:
        changes.append(f"на {destination} теперь {PIECE_NAMES[moved.piece_type]} {COLOUR_GENITIVE[moved.color]}")
    captured = _captured_piece_type(before, move)
    if captured is not None:
        taker = "вы взяли" if before.turn == player else "я взяла"
        changes.append(f"{taker} {PIECE_NAMES_ACCUSATIVE[captured]}")
    if before.is_castling(move):
        changes.append("ладья перешла через короля")
    # Promotion and check are already spoken by `describe_last_move`; listing
    # them again would only make the answer longer to hear.
    return Speech.of(f"{describe_last_move(board).text} Изменения: {', '.join(changes)}.")


_ANSWERS = {
    GameFact.COLOR: describe_color,
    GameFact.MOVE_NUMBER: describe_move_number,
    GameFact.MOVES_PLAYED: describe_moves_played,
    GameFact.CAPTURED: describe_captured,
    GameFact.CASTLING: describe_castling,
    GameFact.CHECK_ATTACKERS: describe_check_attackers,
    GameFact.LAST_MOVE_CHANGES: describe_last_move_changes,
}


def _castling_state(board: chess.Board, player: chess.Color, rook_side: chess.Bitboard) -> str:
    """Whether that castling can be played right now, and if not, exactly why."""
    kingside = bool(rook_side & (chess.BB_H1 | chess.BB_H8))
    has_right = board.has_kingside_castling_rights(player) if kingside else board.has_queenside_castling_rights(player)
    if not has_right:
        return "невозможна: право уже потеряно, король или ладья уходили с места"

    king_square = board.king(player)
    rook_square = chess.msb(board.rooks & board.occupied_co[player] & rook_side)
    assert king_square is not None
    if board.is_attacked_by(not player, king_square):
        return "невозможна, пока королю шах"
    if board.occupied & chess.between(king_square, rook_square):
        return "невозможна: между королем и ладьей стоят фигуры"
    transit = chess.square(5 if kingside else 3, chess.square_rank(king_square))
    target = chess.square(6 if kingside else 2, chess.square_rank(king_square))
    for square in (transit, target):
        if board.is_attacked_by(not player, square):
            return f"невозможна: поле {chess.square_name(square)} на пути короля под боем"
    return "возможна"


def _captured_pieces(board: chess.Board) -> dict[chess.Color, list[int]]:
    """What each side has taken, read off a replay of the canonical history."""
    moves = tuple(board.move_stack)
    replay = board.copy(stack=True)
    while replay.move_stack:
        replay.pop()
    taken: dict[chess.Color, list[int]] = {chess.WHITE: [], chess.BLACK: []}
    for move in moves:
        captured = _captured_piece_type(replay, move)
        if captured is not None:
            taken[replay.turn].append(captured)
        replay.push(move)
    return taken


def _captured_piece_type(before: chess.Board, move: chess.Move) -> int | None:
    """The piece `move` takes; en passant takes a pawn that is not on the target."""
    if before.is_en_passant(move):
        return chess.PAWN
    piece = before.piece_at(move.to_square)
    return None if piece is None else piece.piece_type


def _piece_listing(piece_types: list[int]) -> str:
    parts = []
    for piece_type in _CAPTURE_ORDER:
        count = piece_types.count(piece_type)
        if count == 0:
            continue
        forms = _PIECE_COUNT_FORMS[piece_type]
        parts.append(forms[0] if count == 1 else f"{count} {_plural(count, forms)}")
    return ", ".join(parts)


def _plural(count: int, forms: tuple[str, str, str]) -> str:
    if count % 100 in range(11, 15):
        return forms[2]
    if count % 10 == 1:
        return forms[0]
    if count % 10 in (2, 3, 4):
        return forms[1]
    return forms[2]
