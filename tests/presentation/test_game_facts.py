"""Factual answers about the game are pure: same history, same words."""

from __future__ import annotations

import chess
import pytest

from yura_chess.presentation.game_facts import GameFact, answer_game_fact

SCOTCH = ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6")


def board_of(*moves: str) -> chess.Board:
    board = chess.Board()
    for uci in moves:
        board.push(chess.Move.from_uci(uci))
    return board


def answer(utterance: str, board: chess.Board, player: chess.Color = chess.WHITE) -> tuple[GameFact, str]:
    result = answer_game_fact(utterance, board, player)
    assert result is not None
    return result.fact, result.speech.text


def test_a_question_about_something_else_is_not_a_game_fact() -> None:
    assert answer_game_fact("какая позиция", board_of(), chess.WHITE) is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("за кого я играю", GameFact.COLOR),
        ("каким цветом я играю", GameFact.COLOR),
        ("какой сейчас ход", GameFact.MOVE_NUMBER),
        ("сколько ходов сыграно", GameFact.MOVES_PLAYED),
        ("какие фигуры съедены", GameFact.CAPTURED),
        ("могу ли я рокироваться", GameFact.CASTLING),
        ("возможна ли рокировка", GameFact.CASTLING),
        ("кто дает шах", GameFact.CHECK_ATTACKERS),
        ("что изменил последний ход", GameFact.LAST_MOVE_CHANGES),
    ],
)
def test_every_supported_question_is_recognised(utterance: str, expected: GameFact) -> None:
    assert answer(utterance, board_of(*SCOTCH))[0] is expected


def test_color_names_both_sides_and_whose_turn_it_is() -> None:
    _, text = answer("за кого я играю", board_of("e2e4"), chess.BLACK)
    assert "Вы играете черными" in text
    assert "ваш ход" in text


def test_move_number_follows_the_full_move_counter() -> None:
    assert "4-й ход" in answer("какой сейчас ход", board_of(*SCOTCH))[1]
    assert "1-й ход" in answer("какой сейчас ход", board_of())[1]


def test_moves_played_counts_plies_and_full_moves() -> None:
    assert answer("сколько ходов сыграно", board_of())[1] == "Ходов еще не было."
    assert "ни одного полного хода" in answer("сколько ходов сыграно", board_of("e2e4"))[1]
    assert answer("сколько ходов сыграно", board_of("e2e4", "e7e5"))[1] == "Сыграно 2 хода, это 1 полный ход."
    assert answer("сколько ходов сыграно", board_of(*SCOTCH))[1] == "Сыграно 6 ходов, это 3 полных хода."


def test_captures_are_read_from_the_history_of_both_sides() -> None:
    assert "никто не снял" in answer("какие фигуры съедены", board_of(*SCOTCH))[1]

    board = board_of("e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5e5", "c3b5", "e5e2", "f1e2")
    _, text = answer("какие фигуры съедены", board)
    assert "Вы взяли: ферзь, пешка." in text
    assert "Я взяла: пешка." in text


def test_en_passant_capture_is_counted_even_though_the_target_is_empty() -> None:
    board = board_of("e2e4", "a7a6", "e4e5", "d7d5", "e5d6")
    assert "Вы взяли: пешка." in answer("какие фигуры съедены", board)[1]


def test_castling_reports_the_concrete_reason_it_is_unavailable() -> None:
    _, text = answer("могу ли я рокироваться", board_of(*SCOTCH))
    assert "Короткая рокировка возможна" in text
    assert "Длинная рокировка невозможна: между королем и ладьей стоят фигуры" in text

    moved_king = board_of("e2e4", "e7e5", "e1e2", "a7a6", "e2e1", "a6a5")
    assert "право уже потеряно" in answer("могу ли я рокироваться", moved_king)[1]

    in_check = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert "пока королю шах" in answer("могу ли я рокироваться", in_check)[1]

    attacked_path = chess.Board("r4rk1/8/8/8/8/8/8/R3K2R w KQ - 0 1")
    _, reason = answer("могу ли я рокироваться", attacked_path)
    assert "Короткая рокировка невозможна: поле f1 на пути короля под боем" in reason
    assert "Длинная рокировка возможна" in reason


def test_check_names_the_attacking_pieces() -> None:
    assert answer("кто дает шах", board_of(*SCOTCH))[1] == "Сейчас шаха нет."

    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    _, text = answer("кто дает шах", board)
    assert "Шах вашему королю на e1" in text
    assert "Шах дает: ферзь h4." in text


def test_last_move_changes_name_the_freed_and_occupied_squares() -> None:
    assert answer("что изменилось", board_of())[1] == "Ходов еще не было."

    _, text = answer("что изменилось", board_of("e2e4", "d7d5", "e4d5"))
    assert "поле e4 освободилось" in text
    assert "на d5 теперь пешка белых" in text
    assert "вы взяли пешку" in text


def test_castling_changes_mention_the_rook_without_repeating_the_move() -> None:
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    board.push(chess.Move.from_uci("e1g1"))
    _, text = answer("что изменилось", board)
    assert "Короткая рокировка." in text
    assert "ладья перешла через короля" in text


def test_the_same_history_always_produces_the_same_answer() -> None:
    questions = ("за кого я играю", "сколько ходов сыграно", "какие фигуры съедены", "могу ли я рокироваться")
    first = board_of(*SCOTCH)
    reloaded = board_of(*SCOTCH)
    assert [answer(question, first) for question in questions] == [answer(question, reloaded) for question in questions]


def test_answering_never_mutates_the_board() -> None:
    board = board_of(*SCOTCH)
    before = board.fen()
    for question in ("сколько ходов сыграно", "какие фигуры съедены", "что изменилось", "могу ли я рокироваться"):
        answer(question, board)
    assert board.fen() == before
    assert len(board.move_stack) == len(SCOTCH)
