"""Say why a described move cannot be played.

Runs only after `resolve()` failed to match any legal move, on the parts the
utterance did pin down: piece, source and destination survive on
`RecognizedMove` precisely for this. The answer names the concrete rule that
stops the move — an empty square, a wrong colour, the first piece blocking the
ray, the king left in check — and falls back to a generic reply only when the
intent itself was too vague to judge.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import chess

from yura_chess.voice.types import RecognizedMove

_PIECE_NAMES: dict[int, str] = {
    chess.PAWN: "пешка",
    chess.KNIGHT: "конь",
    chess.BISHOP: "слон",
    chess.ROOK: "ладья",
    chess.QUEEN: "ферзь",
    chess.KING: "король",
}

_GEOMETRY_HINTS: dict[int, str] = {
    chess.KNIGHT: "конь ходит буквой «г»",
    chess.BISHOP: "слон ходит только по диагонали",
    chess.ROOK: "ладья ходит только по вертикали или горизонтали",
    chess.QUEEN: "ферзь ходит по вертикали, горизонтали или диагонали",
    chess.KING: "король ходит на одно поле",
}

_PROMOTION_PIECES: dict[str, int] = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}


class IllegalReason(StrEnum):
    EMPTY_SOURCE = "empty_source"
    OPPONENT_PIECE = "opponent_piece"
    OCCUPIED_DESTINATION = "occupied_destination"
    BLOCKED_PATH = "blocked_path"
    GEOMETRY = "geometry"
    LEAVES_KING_IN_CHECK = "leaves_king_in_check"
    DOES_NOT_ADDRESS_CHECK = "does_not_address_check"
    PAWN_RULE = "pawn_rule"
    PROMOTION = "promotion"
    EN_PASSANT = "en_passant"
    NO_CAPTURE = "no_capture"
    CASTLING = "castling"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class Explanation:
    reason: IllegalReason
    text: str
    source: str | None = None
    destination: str | None = None
    # The first piece standing in the way, when the move was blocked by one.
    blocker: str | None = None


def explain(recognized: RecognizedMove, board: chess.Board) -> Explanation:
    """Explain why `recognized` is not a legal move in `board`."""
    if recognized.castle_short or recognized.castle_long:
        return _explain_castling(board, kingside=recognized.castle_short)

    destination = _parse_square(recognized.destination)
    if destination is None:
        return _unclear()
    source = _source_square(recognized, board)
    if source is None:
        return _unclear()
    if source == destination:
        return _unclear()

    piece = board.piece_at(source)
    if piece is None:
        return Explanation(
            IllegalReason.EMPTY_SOURCE,
            f"На поле {_name(source)} нет фигуры.",
            source=_name(source),
            destination=_name(destination),
        )
    if piece.color != board.turn:
        return Explanation(
            IllegalReason.OPPONENT_PIECE,
            f"На поле {_name(source)} стоит фигура соперника — {_PIECE_NAMES[piece.piece_type]}.",
            source=_name(source),
            destination=_name(destination),
        )

    occupant = board.piece_at(destination)
    if occupant is not None and occupant.color == board.turn:
        return Explanation(
            IllegalReason.OCCUPIED_DESTINATION,
            f"Поле {_name(destination)} занято вашей фигурой — там стоит {_PIECE_NAMES[occupant.piece_type]}.",
            source=_name(source),
            destination=_name(destination),
        )

    promotion = _promotion(recognized, piece, destination)
    move = chess.Move(source, destination, promotion=promotion)
    if board.is_pseudo_legal(move):
        return (
            _explain_king_safety(board, move)
            if move not in board.legal_moves
            else _explain_pseudo_legal(recognized, board, move)
        )

    if piece.piece_type is chess.PAWN:
        return _explain_pawn(board, piece, source, destination)
    return _explain_geometry(board, piece, source, destination)


def _explain_pseudo_legal(recognized: RecognizedMove, board: chess.Board, move: chess.Move) -> Explanation:
    """The move itself is legal, so the utterance described something else wrong."""
    if recognized.capture and not board.is_capture(move):
        destination = _name(move.to_square)
        return Explanation(
            IllegalReason.NO_CAPTURE,
            f"На поле {destination} брать некого.",
            source=_name(move.from_square),
            destination=destination,
        )
    if recognized.promotion:
        return Explanation(
            IllegalReason.PROMOTION,
            "Превращение возможно только когда пешка доходит до последней горизонтали.",
        )
    return _unclear()


def _explain_king_safety(board: chess.Board, move: chess.Move) -> Explanation:
    """A pseudo-legal move that is not legal can only be leaving the king attacked."""
    source, destination = _name(move.from_square), _name(move.to_square)
    if board.is_check():
        return Explanation(
            IllegalReason.DOES_NOT_ADDRESS_CHECK,
            f"Вашему королю сейчас шах, а ход {source} {destination} его не отражает.",
            source=source,
            destination=destination,
        )
    return Explanation(
        IllegalReason.LEAVES_KING_IN_CHECK,
        f"После хода {source} {destination} ваш король окажется под шахом.",
        source=source,
        destination=destination,
    )


def _explain_pawn(board: chess.Board, piece: chess.Piece, source: int, destination: int) -> Explanation:
    source_name, destination_name = _name(source), _name(destination)
    forward = 1 if piece.color is chess.WHITE else -1
    start_rank = 1 if piece.color is chess.WHITE else 6
    file_delta = chess.square_file(destination) - chess.square_file(source)
    rank_delta = chess.square_rank(destination) - chess.square_rank(source)

    if file_delta == 0:
        if board.piece_at(destination) is not None:
            return Explanation(
                IllegalReason.PAWN_RULE,
                f"Пешка не бьет вперед: поле {destination_name} занято.",
                source=source_name,
                destination=destination_name,
            )
        if rank_delta == 2 * forward:
            if chess.square_rank(source) != start_rank:
                return Explanation(
                    IllegalReason.PAWN_RULE,
                    "На два поля пешка ходит только с начальной позиции.",
                    source=source_name,
                    destination=destination_name,
                )
            blocker = chess.square(chess.square_file(source), chess.square_rank(source) + forward)
            return Explanation(
                IllegalReason.BLOCKED_PATH,
                f"Путь закрыт: на поле {_name(blocker)} стоит фигура.",
                source=source_name,
                destination=destination_name,
                blocker=_name(blocker),
            )
        return Explanation(
            IllegalReason.PAWN_RULE,
            f"Пешка ходит вперед на одно поле, а с начальной позиции на два, поэтому {destination_name} недостижимо.",
            source=source_name,
            destination=destination_name,
        )

    # A diagonal step reaches this far only when there is nothing to capture:
    # with a capture available the move would already be pseudo-legal.
    if abs(file_delta) == 1 and rank_delta == forward:
        return Explanation(
            IllegalReason.EN_PASSANT,
            f"По диагонали пешка ходит только со взятием, а на {destination_name} брать некого. "
            "Взятие на проходе возможно только сразу после двойного хода соседней пешки.",
            source=source_name,
            destination=destination_name,
        )

    return Explanation(
        IllegalReason.PAWN_RULE,
        f"Пешка так не ходит: с {source_name} на {destination_name} ей не попасть.",
        source=source_name,
        destination=destination_name,
    )


def _explain_geometry(board: chess.Board, piece: chess.Piece, source: int, destination: int) -> Explanation:
    source_name, destination_name = _name(source), _name(destination)
    if _is_aligned(piece.piece_type, source, destination):
        blocker = _first_blocker(board, source, destination)
        if blocker is not None:
            return Explanation(
                IllegalReason.BLOCKED_PATH,
                f"Путь закрыт: на поле {_name(blocker)} стоит "
                f"{_PIECE_NAMES[board.piece_type_at(blocker) or chess.PAWN]}.",
                source=source_name,
                destination=destination_name,
                blocker=_name(blocker),
            )
    hint = _GEOMETRY_HINTS.get(piece.piece_type, "так эта фигура не ходит")
    return Explanation(
        IllegalReason.GEOMETRY,
        f"С {source_name} на {destination_name} так не пойти: {hint}.",
        source=source_name,
        destination=destination_name,
    )


def _explain_castling(board: chess.Board, kingside: bool) -> Explanation:
    color = board.turn
    king = board.king(color)
    if king is None:  # pragma: no cover - a legal position always has both kings
        return _unclear()
    rights = board.has_kingside_castling_rights(color) if kingside else board.has_queenside_castling_rights(color)
    if not rights:
        return Explanation(
            IllegalReason.CASTLING,
            "Рокировка невозможна: король или ладья уже ходили.",
        )
    if board.is_check():
        return Explanation(IllegalReason.CASTLING, "Рокироваться из-под шаха нельзя.")

    rank = chess.square_rank(king)
    rook = chess.square(7 if kingside else 0, rank)
    blocker = _first_blocker(board, king, rook)
    if blocker is not None:
        return Explanation(
            IllegalReason.CASTLING,
            f"Между королем и ладьей стоит фигура на поле {_name(blocker)}.",
            blocker=_name(blocker),
        )

    step = 1 if kingside else -1
    for offset in (step, 2 * step):
        crossed = chess.square(chess.square_file(king) + offset, rank)
        if board.is_attacked_by(not color, crossed):
            return Explanation(
                IllegalReason.CASTLING,
                f"Король не может пройти через поле {_name(crossed)}: оно под боем.",
                destination=_name(crossed),
            )
    return Explanation(IllegalReason.CASTLING, "Сейчас рокировка невозможна.")


def _source_square(recognized: RecognizedMove, board: chess.Board) -> int | None:
    """Use the named source, or infer it when exactly one own piece fits the hints."""
    named = _parse_square(recognized.source)
    if named is not None:
        return named
    if not recognized.piece:
        return None
    piece_type = chess.PIECE_SYMBOLS.index(recognized.piece.lower())
    candidates = [
        square
        for square in board.pieces(piece_type, board.turn)
        if _matches_hint(square, recognized.source_file, recognized.source_rank)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _matches_hint(square: int, file_hint: str | None, rank_hint: str | None) -> bool:
    name = chess.square_name(square)
    return (file_hint is None or name[0] == file_hint) and (rank_hint is None or name[1] == rank_hint)


def _promotion(recognized: RecognizedMove, piece: chess.Piece, destination: int) -> int | None:
    if piece.piece_type is not chess.PAWN:
        return None
    if chess.square_rank(destination) not in (0, 7):
        return None
    if recognized.promotion is None:
        return chess.QUEEN
    return _PROMOTION_PIECES.get(recognized.promotion)


def _is_aligned(piece_type: int, source: int, destination: int) -> bool:
    file_delta = abs(chess.square_file(destination) - chess.square_file(source))
    rank_delta = abs(chess.square_rank(destination) - chess.square_rank(source))
    straight = file_delta == 0 or rank_delta == 0
    diagonal = file_delta == rank_delta
    if piece_type is chess.ROOK:
        return straight
    if piece_type is chess.BISHOP:
        return diagonal
    return piece_type is chess.QUEEN and (straight or diagonal)


def _first_blocker(board: chess.Board, source: int, destination: int) -> int | None:
    """The first occupied square walking from `source` towards `destination`."""
    file_step = _sign(chess.square_file(destination) - chess.square_file(source))
    rank_step = _sign(chess.square_rank(destination) - chess.square_rank(source))
    file, rank = chess.square_file(source) + file_step, chess.square_rank(source) + rank_step
    while (file, rank) != (chess.square_file(destination), chess.square_rank(destination)):
        square = chess.square(file, rank)
        if board.piece_at(square) is not None:
            return square
        file += file_step
        rank += rank_step
    return None


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _parse_square(name: str | None) -> int | None:
    if not name:
        return None
    try:
        return chess.parse_square(name)
    except ValueError:  # pragma: no cover - the normaliser only builds valid squares
        return None


def _name(square: int) -> str:
    return chess.square_name(square)


def _unclear() -> Explanation:
    return Explanation(
        IllegalReason.UNCLEAR,
        "Я не поняла ход. Назовите фигуру и поле, например «конь эф три».",
    )
