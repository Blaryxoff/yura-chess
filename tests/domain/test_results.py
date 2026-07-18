"""Outcome detection is pure chess logic and needs neither a database nor an engine."""

from __future__ import annotations

import chess
import pytest

from yura_chess.domain.results import GameEnd, automatic_outcome, claimable_draw


@pytest.mark.parametrize(
    ("fen", "end"),
    [
        ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", GameEnd.STALEMATE),
        ("8/8/8/4k3/8/8/4K3/8 w - - 0 1", GameEnd.INSUFFICIENT_MATERIAL),
        ("7R/8/8/4k3/8/8/4K3/8 w - - 150 100", GameEnd.SEVENTY_FIVE_MOVES),
    ],
)
def test_automatic_ends_need_no_claim(fen: str, end: GameEnd) -> None:
    outcome = automatic_outcome(chess.Board(fen))

    assert outcome is not None
    assert outcome.end is end


def test_fivefold_repetition_ends_the_game_automatically() -> None:
    board = chess.Board()
    for _ in range(4):
        for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
            board.push_uci(uci)

    outcome = automatic_outcome(board)

    assert outcome is not None
    assert outcome.end is GameEnd.FIVEFOLD_REPETITION


def test_the_fifty_move_draw_is_claimable_but_not_automatic() -> None:
    board = chess.Board("7R/8/8/4k3/8/8/4K3/8 w - - 100 60")

    assert automatic_outcome(board) is None
    assert claimable_draw(board) is GameEnd.FIFTY_MOVES


def test_checkmate_names_the_side_that_delivered_it() -> None:
    board = chess.Board()
    for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
        board.push_uci(uci)

    outcome = automatic_outcome(board)

    assert outcome is not None
    assert outcome.end is GameEnd.CHECKMATE
    assert outcome.winner is not None
    assert outcome.winner.value == "black"
