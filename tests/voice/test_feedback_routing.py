import chess
import pytest

from yura_chess.application.command_router import CommandKind, route


@pytest.mark.parametrize(
    "phrase",
    [
        "Где оставить отзыв?",
        "Как написать отзыв?",
        "Где можно оставить отзыв о навыке?",
        "Как мне оставить отзыв?",
        "Хочу написать отзыв",
        "Оставить отзыв",
        "Написать отзыв об игре",
        "Где поставить оценку?",
        "Отзыв",
        "Алиса, где оставить отзыв?",
    ],
)
def test_feedback_phrases_are_commands_without_or_during_a_game(phrase: str) -> None:
    for board in (None, chess.Board()):
        assert route(phrase, board).kind is CommandKind.FEEDBACK


@pytest.mark.parametrize(
    "phrase",
    [
        "Не проси отзывы",
        "Не проси отзыв",
        "Больше не проси отзывы",
        "Не просите отзывы",
        "Не напоминай мне об отзывах",
        "Отключи просьбы об отзывах",
        "Выключи просьбы об отзывах",
    ],
)
def test_review_prompt_opt_out_phrases_are_distinct_from_chess_commands(phrase: str) -> None:
    assert route(phrase, chess.Board()).kind is CommandKind.FEEDBACK_DISMISS


@pytest.mark.parametrize(
    "phrase", ["разбери партию", "оцени позицию", "не хочу оставлять отзыв", "нет", "е два е четыре"]
)
def test_feedback_does_not_capture_other_commands_or_negative_requests(phrase: str) -> None:
    assert route(phrase, chess.Board()).kind not in {CommandKind.FEEDBACK, CommandKind.FEEDBACK_DISMISS}
