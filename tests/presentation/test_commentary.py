"""Commentary is rare, whitelisted and derived from the history alone."""

from __future__ import annotations

import chess

from yura_chess.domain.analysis import BLUNDER_CENTIPAWNS
from yura_chess.domain.game import PlayerColor
from yura_chess.domain.preferences import DetailLevel
from yura_chess.presentation.commentary import Comment, CommentCategory, comment_on

START = chess.STARTING_FEN

# 1.e4 e5 2.Nf3 Nc6 3.Bc4 — quiet development, nothing worth saying about the last move.
QUIET = ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4")

# 1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6 4.Qxf7 — mate would end the game, so stop before it.
CHECK_LINE = ("e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7")

# A White rook on a1 against a hanging Black rook on h1 that h8 defends.
ROOKS = "4k2r/8/8/8/8/8/4K3/R6r w k - 0 1"


def comment(
    moves: tuple[str, ...],
    color: PlayerColor = PlayerColor.WHITE,
    detail_level: DetailLevel = DetailLevel.NORMAL,
    losses: dict[int, int] | None = None,
    fen: str = START,
) -> Comment | None:
    return comment_on(fen, moves, color, detail_level, losses)


def test_a_quiet_developing_move_is_not_commented() -> None:
    assert comment(QUIET) is None


def test_a_check_is_named_from_the_players_side() -> None:
    given = comment(CHECK_LINE)
    assert given is not None
    assert given.category is CommentCategory.CHECK
    assert given.text == "Вы объявили шах."

    theirs = comment(CHECK_LINE, PlayerColor.BLACK)
    assert theirs is not None
    assert theirs.text == "Вам шах."


def test_an_even_trade_stays_silent_but_a_won_piece_does_not() -> None:
    # A rook takes a rook and is taken back by the other one: nothing changed.
    assert comment(("a1h1", "h8h1"), fen=ROOKS) is None

    # The same capture left unanswered is a whole rook up for White.
    given = comment(("a1h1",), fen=ROOKS)
    assert given is not None
    assert given.category is CommentCategory.MATERIAL
    assert given.text == "Вы выиграли материал."
    from_black = comment(("a1h1",), PlayerColor.BLACK, fen=ROOKS)
    assert from_black is not None and from_black.text == "Я выиграла материал."


def test_the_first_known_opening_is_named() -> None:
    given = comment(("e2e4",))
    assert given is not None
    assert given.category is CommentCategory.OPENING
    assert "код" in given.text


def test_leaving_the_opening_is_commented() -> None:
    # 1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.Nc3 Nf6 — the sixth minor piece is out.
    developed = ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "b1c3", "g8f6")
    given = comment(developed)
    assert given is not None
    assert given.category is CommentCategory.STAGE
    assert given.text == "Партия перешла в миттельшпиль."


def test_a_promotion_is_commented() -> None:
    moves = ("a2a4", "b7b5", "a4b5", "g8f6", "b5b6", "f6g8", "b6b7", "g8f6", "b7a8q")
    given = comment(moves)
    assert given is not None
    assert given.category is CommentCategory.PROMOTION
    assert "превратилась в ферзя" in given.text


def test_the_cooldown_silences_a_second_remark_too_soon_after_the_first() -> None:
    # Two checks two plies apart: the second falls inside the cooldown.
    doubled = (*CHECK_LINE, "e8f7", "c4d5")
    assert comment(doubled) is None


def test_the_same_category_is_never_raised_twice_in_a_row() -> None:
    # A second check well past the cooldown is still the same subject.
    repeated = (*CHECK_LINE, "e8f7", "c4d5", "f7e8", "d5c4", "e8e7", "c4b5")
    assert comment(repeated) is None


def test_an_engine_gain_is_commented_only_when_the_loss_map_says_so() -> None:
    moves = QUIET
    ply = len(moves) - 1
    assert comment(moves, losses={ply: -BLUNDER_CENTIPAWNS}) is not None
    assert comment(moves, losses={ply: -BLUNDER_CENTIPAWNS + 1}) is None
    # A costly move is the training warning's subject, not commentary's.
    assert comment(moves, losses={ply: BLUNDER_CENTIPAWNS}) is None
    assert comment(moves) is None


def test_brief_answers_drop_commentary_without_changing_the_history() -> None:
    assert comment(CHECK_LINE, detail_level=DetailLevel.BRIEF) is None
    assert comment(CHECK_LINE, detail_level=DetailLevel.DETAILED) is not None


def test_the_same_history_always_produces_the_same_remark() -> None:
    first = comment(CHECK_LINE)
    second = comment(CHECK_LINE)
    assert first == second


def test_an_unplayed_game_has_nothing_to_comment_on() -> None:
    assert comment(()) is None
