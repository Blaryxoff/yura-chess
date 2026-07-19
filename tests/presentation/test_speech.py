"""Everything the skill says must be complete without a screen.

The pronunciation is checked against the normaliser: whatever the skill spells
out has to parse back into the same square, otherwise the player cannot repeat
what they just heard.
"""

from __future__ import annotations

import chess
import pytest

from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.domain.results import GameEnd, GameOutcome, TurnResult, TurnStatus
from yura_chess.presentation.move_speech import (
    Speech,
    describe_move,
    describe_played_move,
    spell_slowly,
    spell_square,
)
from yura_chess.presentation.position_speech import (
    PAGE_COUNT,
    PositionQuery,
    answer_position_query,
    read_board,
)
from yura_chess.presentation.response_composer import compose_turn
from yura_chess.voice.normalizer import normalize
from yura_chess.voice.types import TokenKind

PROMOTION_FEN = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
EN_PASSANT_FEN = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
CASTLING_FEN = "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1"
CAPTURE_FEN = "4k3/8/8/8/8/8/8/R2q1K2 w - - 0 1"
MATE_IN_ONE_FEN = "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1"


def _result(status: TurnStatus, **kwargs: object) -> TurnResult:
    defaults: dict[str, object] = {
        "game_id": "g1",
        "revision": 1,
        "fen": chess.STARTING_FEN,
        "moves": (),
        "player_color": PlayerColor.WHITE,
        "game_status": GameStatus.ACTIVE,
    }
    return TurnResult(status=status, **{**defaults, **kwargs})  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["a1", "e4", "g7", "h8", "c3", "f2", "b5", "d6"])
def test_spelled_squares_parse_back_into_the_same_square(name: str) -> None:
    signature = normalize(spell_square(name)).signature

    assert len(signature) == 1
    assert signature[0].kind is TokenKind.SQUARE
    assert signature[0].value == name


def test_display_text_keeps_algebraic_squares_and_tts_spells_them() -> None:
    speech = describe_move(chess.Board(), chess.Move.from_uci("e2e4"))

    assert speech.text == "пешка e2 e4."
    assert speech.tts == "пешка е два е четыре."


def test_no_separate_tts_when_pronunciation_matches_the_text() -> None:
    speech = Speech.of("Партия окончена.")

    assert speech.tts is None
    assert speech.spoken() == "Партия окончена."


def test_capture_check_castling_and_promotion_are_named_unambiguously() -> None:
    capture = describe_move(chess.Board(CAPTURE_FEN), chess.Move.from_uci("a1d1"))
    castling = describe_move(chess.Board(CASTLING_FEN), chess.Move.from_uci("e1c1"))
    promotion = describe_move(chess.Board(PROMOTION_FEN), chess.Move.from_uci("a7a8q"))
    en_passant = describe_move(chess.Board(EN_PASSANT_FEN), chess.Move.from_uci("e5d6"))
    mate = describe_move(chess.Board(MATE_IN_ONE_FEN), chess.Move.from_uci("a1a8"))

    assert capture.text == "ладья a1 берет ферзя на d1."
    assert castling.text == "Длинная рокировка."
    assert "и превращается в ферзя" in promotion.text
    assert "на проходе" in en_passant.text
    assert mate.text.endswith("Мат.")


def test_move_described_from_the_resulting_position_names_the_piece() -> None:
    board = chess.Board()
    board.push_uci("g1f3")

    assert describe_played_move(board, chess.Move.from_uci("g1f3")).text == "конь g1 f3."


def test_square_contents_can_be_asked_for() -> None:
    answer = answer_position_query("что на е пять", chess.Board(EN_PASSANT_FEN))
    empty = answer_position_query("какая фигура на а один", chess.Board(EN_PASSANT_FEN))

    assert answer.query is PositionQuery.SQUARE
    assert answer.speech.text == "На e5 — пешка белых."
    assert empty.speech.text == "Поле a1 пустое."


def test_piece_kind_locations_can_be_asked_for_one_side() -> None:
    answer = answer_position_query("где стоят белые ладьи", chess.Board(CASTLING_FEN))

    assert answer.query is PositionQuery.PIECE_KIND
    assert answer.speech.text == "Белые ладьи: a1, h1."


def test_piece_kind_reports_both_sides_when_no_colour_is_named() -> None:
    answer = answer_position_query("где ладьи", chess.Board(CASTLING_FEN))

    assert "белые ладьи: a1, h1".capitalize() in answer.speech.text
    assert "ладьи черных нет" in answer.speech.text


def test_all_pieces_of_one_side_can_be_asked_for() -> None:
    answer = answer_position_query("какие фигуры у черных", chess.Board(CASTLING_FEN))

    assert answer.query is PositionQuery.SIDE
    assert answer.speech.text == "У черных: король e8."


def test_whole_position_is_read_in_stable_groups_with_a_continuation() -> None:
    board = chess.Board()
    first = answer_position_query("прочитай всю позицию", board)
    second = answer_position_query("дальше", board, page=first.page)

    assert first.query is PositionQuery.WHOLE_BOARD
    assert first.page == 0 and first.has_next
    assert "Восьмая горизонталь" in first.speech.text and "Седьмая горизонталь" in first.speech.text
    assert second.page == 1
    assert "Шестая горизонталь пуста." in second.speech.text
    # Stable grouping: the same page always holds the same ranks.
    assert read_board(board, 0).speech == first.speech


def test_last_group_offers_no_continuation() -> None:
    last = read_board(chess.Board(), PAGE_COUNT - 1)

    assert not last.has_next
    assert "дальше" not in last.speech.text
    assert "Первая горизонталь" in last.speech.text


def test_slow_repeat_spells_the_coordinate_and_leaves_the_board_untouched() -> None:
    board = chess.Board()
    before = board.fen()
    answer = answer_position_query("повтори медленно е четыре", board)

    assert answer.query is PositionQuery.SLOW_SQUARE
    assert answer.speech.text == "Поле e4: вертикаль e, горизонталь 4."
    assert answer.speech.tts == spell_slowly("e4").tts
    assert "Вертикаль — е" in answer.speech.spoken()
    assert board.fen() == before


def test_last_move_turn_and_check_can_be_asked_by_voice() -> None:
    board = chess.Board()
    no_move = answer_position_query("какой последний ход", board)
    board.push_uci("e2e4")
    last_move = answer_position_query("какой был последний ход", board)
    turn = answer_position_query("чей ход", board)
    no_check = answer_position_query("есть ли шах сейчас", board)
    checked = chess.Board("4k3/8/8/8/8/8/4R3/4K3 b - - 0 1")
    check = answer_position_query("кто под шахом", checked)

    assert no_move.query is PositionQuery.LAST_MOVE
    assert no_move.speech.text == "Ходов еще не было."
    assert last_move.speech.text == "Последний ход: пешка e2 e4."
    assert turn.speech.text == "Сейчас ход черных."
    assert no_check.speech.text == "Сейчас шаха нет."
    assert check.speech.text == "Шах черному королю."


def test_previous_moves_can_be_selected_by_distance_and_colour() -> None:
    board = chess.Board()
    for move in ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6"):
        board.push_uci(move)

    fourth_black = answer_position_query("что сделали черные четыре хода назад", board)
    fourth_overall = answer_position_query("что было четыре хода назад", board)
    last_black = answer_position_query("какой был последний ход черных", board)

    assert fourth_black.query is PositionQuery.HISTORY
    assert "пешка e7 e5" in fourth_black.speech.text
    assert "слон f1 b5" in fourth_overall.speech.text
    assert "конь g8 f6" in last_black.speech.text


def test_history_query_reports_when_the_game_is_too_short() -> None:
    board = chess.Board()
    board.push_uci("e2e4")

    answer = answer_position_query("что сделали черные два хода назад", board)

    assert answer.query is PositionQuery.HISTORY
    assert answer.speech.text == "Не могу найти такой ход: в партии у черных было только 0 ходов."


def test_engine_move_answer_is_complete_without_any_screen_information() -> None:
    board = chess.Board()
    speech = compose_turn(_result(TurnStatus.OK, engine_move="e2e4"), board)

    assert speech.text == "Мой ход. пешка e2 e4."
    assert speech.tts == "Мой ход. пешка е два е четыре."


def test_engine_move_is_described_without_the_previous_position() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    speech = compose_turn(_result(TurnStatus.OK, engine_move="e2e4", fen=board.fen()))

    assert speech.text == "Мой ход. пешка e2 e4."


def test_pending_engine_reply_tells_the_player_the_move_is_kept() -> None:
    speech = compose_turn(_result(TurnStatus.ENGINE_UNAVAILABLE))

    assert "записан" in speech.text
    assert "продолжаем" in speech.text


def test_checkmate_names_the_winner_from_the_player_side() -> None:
    speech = compose_turn(
        _result(
            TurnStatus.GAME_OVER,
            outcome=GameOutcome(GameEnd.CHECKMATE, PlayerColor.BLACK),
            game_status=GameStatus.FINISHED,
        )
    )

    assert speech.text == "Мат. Черные выиграли. Вы проиграли."


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        (GameEnd.STALEMATE, "Пат. Ничья."),
        (GameEnd.FIFTY_MOVES, "Правило пятидесяти ходов. Ничья."),
        (GameEnd.THREEFOLD_REPETITION, "Троекратное повторение позиции. Ничья."),
    ],
)
def test_draws_are_named_by_their_rule(end: GameEnd, expected: str) -> None:
    speech = compose_turn(_result(TurnStatus.GAME_OVER, outcome=GameOutcome(end), game_status=GameStatus.FINISHED))

    assert speech.text == expected


@pytest.mark.parametrize(
    "status",
    [
        TurnStatus.NOT_PLAYER_TURN,
        TurnStatus.GAME_ALREADY_FINISHED,
        TurnStatus.DRAW_NOT_CLAIMABLE,
        TurnStatus.UNDO_REJECTED,
        TurnStatus.ILLEGAL_MOVE,
        TurnStatus.OK,
    ],
)
def test_every_status_produces_a_non_empty_spoken_answer(status: TurnStatus) -> None:
    speech = compose_turn(_result(status))

    assert speech.text
    assert speech.spoken()
