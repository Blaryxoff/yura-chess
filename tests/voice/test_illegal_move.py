"""Explanations of moves that cannot be played.

Each position isolates one rule, so a failing test names the rule that broke.
The router tests check the other half of the contract: an explanation is only
ever produced after legal matching failed.
"""

from __future__ import annotations

import chess
import pytest

from yura_chess.application.command_router import CommandKind, route
from yura_chess.voice.illegal_move import Explanation, IllegalReason, explain
from yura_chess.voice.types import RecognizedMove

BLOCKED_ROOK_FEN = "4k3/8/8/8/8/8/3P4/3RK3 w - - 0 1"
OPEN_PIECES_FEN = "4k3/8/8/8/8/1P6/8/R2QKB2 w - - 0 1"
PINNED_FEN = "4r2k/8/8/8/8/8/4B3/4K3 w - - 0 1"
IN_CHECK_FEN = "4k3/8/8/b7/8/8/5P2/4K2R w - - 0 1"
PAWN_FEN = "4k3/8/8/8/8/4p3/4P1P1/4K3 w - - 0 1"
DOUBLE_BLOCKED_FEN = "4k3/8/8/8/8/4n3/4P3/4K3 w - - 0 1"
PROMOTION_FEN = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
CASTLING_BLOCKED_FEN = "4k3/8/8/8/8/8/8/R2BK1NR w KQ - 0 1"
CASTLING_ATTACKED_FEN = "4k3/8/8/8/5q2/8/8/R3K2R w KQ - 0 1"
CASTLING_LOST_FEN = "4k3/8/8/8/8/8/8/R3K2R w - - 0 1"
BLACK_TO_MOVE_FEN = "4k3/4p3/8/8/8/8/4P3/4K3 b - - 0 1"


def _explain(utterance: str, fen: str) -> Explanation:
    board = chess.Board(fen)
    routed = route(utterance, board)

    assert routed.kind is CommandKind.ILLEGAL_MOVE
    assert routed.explanation is not None
    return routed.explanation


def test_empty_source_square_is_named() -> None:
    explanation = _explain("ладья а один а пять", BLOCKED_ROOK_FEN)

    assert explanation.reason is IllegalReason.EMPTY_SOURCE
    assert "a1" in explanation.text


def test_moving_an_opponent_piece_is_named_with_its_colour() -> None:
    explanation = _explain("пешка е семь е шесть", BLACK_TO_MOVE_FEN.replace(" b ", " w "))

    assert explanation.reason is IllegalReason.OPPONENT_PIECE
    assert "соперника" in explanation.text


def test_destination_occupied_by_own_piece() -> None:
    explanation = _explain("ладья дэ один дэ два", BLOCKED_ROOK_FEN)

    assert explanation.reason is IllegalReason.OCCUPIED_DESTINATION
    assert explanation.destination == "d2"


def test_rook_reports_the_first_blocking_square() -> None:
    explanation = _explain("ладья дэ один дэ пять", BLOCKED_ROOK_FEN)

    assert explanation.reason is IllegalReason.BLOCKED_PATH
    assert explanation.blocker == "d2"


def test_bishop_reports_the_first_blocking_square() -> None:
    board = chess.Board("4k3/8/8/8/8/8/4P3/3B1K2 w - - 0 1")

    explanation = explain(RecognizedMove(piece="B", source="d1", destination="g4"), board)

    assert explanation.reason is IllegalReason.BLOCKED_PATH
    assert explanation.blocker == "e2"


def test_queen_reports_the_first_blocking_square() -> None:
    explanation = _explain("ферзь дэ один а четыре", OPEN_PIECES_FEN)

    assert explanation.reason is IllegalReason.BLOCKED_PATH
    assert explanation.blocker == "b3"


@pytest.mark.parametrize(
    ("utterance", "hint"),
    [
        ("ладья а один це три", "ладья"),
        ("слон эф один дэ два", "слон"),
        ("ферзь дэ один е три", "ферзь"),
    ],
)
def test_geometry_of_each_sliding_piece_is_explained(utterance: str, hint: str) -> None:
    explanation = _explain(utterance, OPEN_PIECES_FEN)

    assert explanation.reason is IllegalReason.GEOMETRY
    assert hint in explanation.text


def test_knight_geometry_is_explained() -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/4KN2 w - - 0 1")

    explanation = explain(RecognizedMove(piece="N", source="f1", destination="f4"), board)

    assert explanation.reason is IllegalReason.GEOMETRY
    assert "буквой" in explanation.text


def test_pinned_piece_would_leave_the_king_in_check() -> None:
    explanation = _explain("слон е два дэ три", PINNED_FEN)

    assert explanation.reason is IllegalReason.LEAVES_KING_IN_CHECK
    assert "шахом" in explanation.text


def test_a_move_that_ignores_the_current_check_is_explained() -> None:
    explanation = _explain("ладья аш один аш два", IN_CHECK_FEN)

    assert explanation.reason is IllegalReason.DOES_NOT_ADDRESS_CHECK
    assert "шах" in explanation.text


def test_pawn_does_not_capture_forward() -> None:
    explanation = _explain("пешка е два е три", PAWN_FEN)

    assert explanation.reason is IllegalReason.PAWN_RULE
    assert "не бьет вперед" in explanation.text


def test_double_pawn_step_reports_the_blocking_square() -> None:
    explanation = _explain("пешка е два е четыре", DOUBLE_BLOCKED_FEN)

    assert explanation.reason is IllegalReason.BLOCKED_PATH
    assert explanation.blocker == "e3"


def test_double_pawn_step_only_from_the_starting_rank() -> None:
    board = chess.Board("4k3/8/8/8/8/4P3/8/4K3 w - - 0 1")

    explanation = explain(RecognizedMove(piece="P", source="e3", destination="e5"), board)

    assert explanation.reason is IllegalReason.PAWN_RULE
    assert "начальной позиции" in explanation.text


def test_diagonal_pawn_move_without_a_capture_mentions_en_passant() -> None:
    explanation = _explain("пешка жэ два эф три", PAWN_FEN)

    assert explanation.reason is IllegalReason.EN_PASSANT
    assert "на проходе" in explanation.text


def test_a_claimed_capture_on_a_quiet_move_says_the_square_is_empty() -> None:
    explanation = _explain("слон це один бьет аш шесть", "4k3/8/8/8/8/8/8/2B1K3 w - - 0 1")

    assert explanation.reason is IllegalReason.NO_CAPTURE
    assert "брать некого" in explanation.text


def test_pawn_geometry_beyond_its_reach() -> None:
    board = chess.Board(PAWN_FEN)

    explanation = explain(RecognizedMove(piece="P", source="e2", destination="e5"), board)

    assert explanation.reason is IllegalReason.PAWN_RULE
    assert explanation.destination == "e5"


def test_a_capture_to_the_last_rank_with_nothing_to_take_is_not_a_promotion_problem() -> None:
    board = chess.Board("3nk3/P7/8/8/8/8/8/4K3 w - - 0 1")

    explanation = explain(RecognizedMove(piece="P", source="a7", destination="b8"), board)

    assert explanation.reason is IllegalReason.EN_PASSANT
    assert "брать некого" in explanation.text


def test_a_promotion_without_a_named_piece_is_clarified_not_explained() -> None:
    board = chess.Board(PROMOTION_FEN)

    routed = route("пешка а семь а восемь", board)

    assert routed.kind is CommandKind.CLARIFY
    assert routed.explanation is None


def test_promotion_claimed_away_from_the_last_rank() -> None:
    board = chess.Board(PROMOTION_FEN)

    explanation = explain(RecognizedMove(piece="K", source="e1", destination="e2", promotion="q"), board)

    assert explanation.reason is IllegalReason.PROMOTION
    assert "последней горизонтали" in explanation.text


def test_castling_through_an_occupied_square() -> None:
    explanation = _explain("короткая рокировка", CASTLING_BLOCKED_FEN)

    assert explanation.reason is IllegalReason.CASTLING
    assert explanation.blocker == "g1"


def test_long_castling_through_an_occupied_square() -> None:
    explanation = _explain("длинная рокировка", CASTLING_BLOCKED_FEN)

    assert explanation.reason is IllegalReason.CASTLING
    assert explanation.blocker == "d1"


def test_castling_through_an_attacked_square() -> None:
    explanation = _explain("короткая рокировка", CASTLING_ATTACKED_FEN)

    assert explanation.reason is IllegalReason.CASTLING
    assert explanation.destination == "f1"


def test_castling_after_the_rights_are_gone() -> None:
    explanation = _explain("короткая рокировка", CASTLING_LOST_FEN)

    assert explanation.reason is IllegalReason.CASTLING
    assert "уже ходили" in explanation.text


def test_castling_out_of_check() -> None:
    explanation = _explain("короткая рокировка", "4k3/8/8/8/7b/8/8/R3K2R w KQ - 0 1")

    assert explanation.reason is IllegalReason.CASTLING
    assert "из-под шаха" in explanation.text


def test_source_is_inferred_when_only_one_piece_of_the_type_is_left() -> None:
    board = chess.Board(BLOCKED_ROOK_FEN)

    explanation = explain(RecognizedMove(piece="R", destination="d5"), board)

    assert explanation.reason is IllegalReason.BLOCKED_PATH
    assert explanation.source == "d1"


def test_an_underspecified_intent_falls_back_to_the_generic_reply() -> None:
    board = chess.Board(OPEN_PIECES_FEN)

    explanation = explain(RecognizedMove(destination="e4"), board)

    assert explanation.reason is IllegalReason.UNCLEAR


def test_two_pieces_of_the_same_type_are_too_vague_to_explain() -> None:
    board = chess.Board(CASTLING_LOST_FEN)

    explanation = explain(RecognizedMove(piece="R", destination="d5"), board)

    assert explanation.reason is IllegalReason.UNCLEAR


def test_a_legal_move_is_never_explained_as_illegal() -> None:
    routed = route("пешка е два е четыре", chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.explanation is None


def test_an_ambiguous_legal_move_asks_instead_of_explaining() -> None:
    board = chess.Board("4k3/8/8/8/8/5N2/8/1N2K3 w - - 0 1")

    routed = route("конь дэ два", board)

    assert routed.kind is CommandKind.CLARIFY
    assert routed.explanation is None


def test_an_utterance_without_move_tokens_stays_unknown() -> None:
    routed = route("сегодня хорошая погода", chess.Board())

    assert routed.kind is CommandKind.UNKNOWN
    assert routed.explanation is None


def test_a_piece_cannot_move_to_its_own_square() -> None:
    explanation = explain(RecognizedMove(piece="P", destination="e2"), chess.Board())

    assert explanation.reason is IllegalReason.UNCLEAR
