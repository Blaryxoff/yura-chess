"""Opening naming and stage detection are read-only and deterministic."""

from __future__ import annotations

import chess
import pytest

from yura_chess.presentation.opening import (
    GameStage,
    describe_opening,
    describe_stage,
    game_stage,
    identify_opening,
)

RUY_LOPEZ = ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5")


def board_of(*moves: str, fen: str | None = None) -> chess.Board:
    board = chess.Board() if fen is None else chess.Board(fen)
    for uci in moves:
        board.push(chess.Move.from_uci(uci))
    return board


def test_the_longest_known_prefix_wins() -> None:
    short = identify_opening(board_of(*RUY_LOPEZ[:3]))
    long = identify_opening(board_of(*RUY_LOPEZ))
    assert short is not None and long is not None
    assert long.opening == "Ruy Lopez"
    assert long != short


def test_a_name_survives_moves_that_leave_the_book() -> None:
    known = identify_opening(board_of(*RUY_LOPEZ, "h7h6", "h2h3", "h6h5", "h3h4"))
    assert known is not None
    assert known.opening == "Ruy Lopez"


def test_an_unplayed_game_has_no_opening() -> None:
    assert identify_opening(board_of()) is None
    assert describe_opening(board_of()).text == "Дебют не определён."


def test_a_game_that_did_not_start_from_the_initial_position_has_no_opening() -> None:
    board = board_of("e2e4", fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    assert identify_opening(board) is not None
    assert identify_opening(board_of("e3e4", fen="8/8/4k3/8/8/4K3/8/8 w - - 0 1")) is None


def test_the_spoken_opening_names_the_variation_and_the_eco_code() -> None:
    speech = describe_opening(board_of(*RUY_LOPEZ, "a7a6"))
    assert "Испанская партия" in speech.text
    assert "защита Морфи" in speech.text
    assert "код C7" in speech.text


def test_naming_the_opening_does_not_touch_the_board() -> None:
    board = board_of(*RUY_LOPEZ)
    before = board.fen(), list(board.move_stack)
    identify_opening(board)
    describe_opening(board)
    describe_stage(board)
    assert (board.fen(), list(board.move_stack)) == before


def test_the_start_of_the_game_is_the_opening() -> None:
    assert game_stage(board_of()) is GameStage.OPENING
    assert game_stage(board_of(*RUY_LOPEZ)) is GameStage.OPENING


def test_an_early_queen_exchange_is_not_yet_an_endgame() -> None:
    # Both sides still keep two rooks and four minor pieces: far above the
    # endgame threshold, however early the queens came off.
    traded = board_of("e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5d1", "e1d1")
    assert game_stage(traded) is not GameStage.ENDGAME


def test_developed_pieces_end_the_opening_before_the_move_limit() -> None:
    board = board_of("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "b1c3", "f8c5", "d2d3", "d7d6", "c1e3", "c8e6")
    assert game_stage(board) is GameStage.MIDDLEGAME


def test_a_long_game_with_pieces_at_home_is_still_not_the_opening() -> None:
    board = chess.Board()
    for uci in ("a2a3", "a7a6", "b2b3", "b7b6", "a3a4", "a6a5", "b3b4", "b6b5", "h2h3", "h7h6"):
        board.push(chess.Move.from_uci(uci))
    for uci in ("h3h4", "h6h5", "g2g3", "g7g6", "g3g4", "g6g5", "d2d3", "d7d6", "e2e3", "e7e6"):
        board.push(chess.Move.from_uci(uci))
    assert game_stage(board) is GameStage.MIDDLEGAME


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        # Rook and a knight each: an endgame by material alone.
        ("4k3/8/8/8/8/8/4P3/R3K1N1 w - - 0 30", GameStage.ENDGAME),
        # Queen against queen: still an endgame, both sides hold nine points.
        ("3qk3/8/8/8/8/8/8/3QK3 w - - 0 30", GameStage.ENDGAME),
        # A queen and a rook are fourteen points: one over the threshold.
        ("4k3/8/8/8/8/8/8/3QK2R w - - 0 30", GameStage.MIDDLEGAME),
    ],
)
def test_material_decides_the_endgame(fen: str, expected: GameStage) -> None:
    assert game_stage(chess.Board(fen)) is expected


def test_the_spoken_stage_names_it_in_russian() -> None:
    assert describe_stage(board_of()).text == "Сейчас дебют."
    assert describe_stage(chess.Board("4k3/8/8/8/8/8/8/4K1N1 w - - 0 40")).text == "Сейчас эндшпиль."
