"""Voice recognition of moves, from normalisation to routing.

Phrasings are morphological variants and the standard Russian pronunciation of
the files; captured Alice transcripts fold in as extra parametrised rows.
"""

from __future__ import annotations

import chess
import pytest

from yura_chess.application.command_router import (
    CommandKind,
    LevelIntent,
    LevelRequest,
    PendingClarification,
    PersonaWish,
    PreferenceChange,
    PuzzleQuestion,
    RematchColor,
    RematchRequest,
    ReviewQuestion,
    ScreenWish,
    TrainingQuestion,
    confirmation_answer,
    route,
)
from yura_chess.domain.preferences import BoardOrientation, DetailLevel, NotationStyle, PauseStyle
from yura_chess.presentation.response_composer import NEXT_STEP_PROMPT
from yura_chess.voice.move_resolver import recognize, resolve
from yura_chess.voice.normalizer import normalize
from yura_chess.voice.types import ResolutionStatus, TokenKind

TWO_KNIGHTS_FEN = "4k3/8/8/8/8/5N2/8/1N2K3 w - - 0 1"
TWO_ROOKS_FEN = "4k3/8/8/8/4K3/8/8/R6R w - - 0 1"
CASTLING_FEN = "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1"
PROMOTION_FEN = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
CAPTURE_FEN = "4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1"


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("пешка е два е четыре", "e2e4"),
        ("пешкой с е два на е четыре", "e2e4"),
        ("ходи пешкой на е четыре", "e2e4"),
        ("е два е четыре", "e2e4"),
        ("конь бэ один це три", "b1c3"),
        ("конем на эф три", "g1f3"),
        ("конь жэ один эф три", "g1f3"),
        ("пешка дэ два дэ четыре", "d2d4"),
        # ASR returns coordinates glued to the digit, in Cyrillic or in Latin.
        ("е2 е4", "e2e4"),
        ("e2 e4", "e2e4"),
        ("d2d4", "d2d4"),
    ],
)
def test_resolves_opening_moves(utterance: str, expected: str) -> None:
    resolution = resolve(normalize(utterance), chess.Board())

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == expected


def test_bare_destination_resolves_when_only_one_move_reaches_it() -> None:
    resolution = resolve(normalize("е четыре"), chess.Board())

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == "e2e4"
    # A destination alone is the least specific reading it can be.
    assert resolution.confidence == pytest.approx(0.75)


def test_full_coordinates_outrank_a_piece_and_destination() -> None:
    coordinates = resolve(normalize("е два е четыре"), chess.Board())
    named = resolve(normalize("пешка на е четыре"), chess.Board())

    assert coordinates.confidence > named.confidence


def test_unknown_words_lower_confidence() -> None:
    clean = resolve(normalize("пешка е два е четыре"), chess.Board())
    noisy = resolve(normalize("пешка е два е четыре тарарам"), chess.Board())

    assert noisy.status is ResolutionStatus.RESOLVED
    assert noisy.move == clean.move
    assert noisy.confidence < clean.confidence


@pytest.mark.parametrize(
    ("fen", "utterance", "expected"),
    [
        (
            "4k3/8/8/2N5/8/8/8/4K3 w - - 0 1",
            "c 5 d 3 конем",
            "c5d3",
        ),
        (
            "4k3/8/8/8/5B2/8/8/4K3 w - - 0 1",
            "ладно я отвлеку слона слон идет слон f 4 е 5",
            "f4e5",
        ),
    ],
)
def test_resolver_extracts_the_only_legal_move_from_conversational_speech(
    fen: str,
    utterance: str,
    expected: str,
) -> None:
    resolution = resolve(normalize(utterance), chess.Board(fen))

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == expected


def test_repeated_destination_does_not_hide_explicit_capture_coordinates() -> None:
    board = chess.Board()
    for move in ("e2e4", "e7e6", "d2d4", "d7d5"):
        board.push_uci(move)

    resolution = resolve(normalize("я бью на d 5 пешкой е 4 бьет d 5"), board)

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == "e4d5"


def test_reference_to_the_engine_castling_does_not_override_the_players_move() -> None:
    board = chess.Board()
    for move in (
        "e2e4",
        "e7e6",
        "d2d4",
        "d7d5",
        "e4d5",
        "e6d5",
        "f1d3",
        "c7c5",
        "d4c5",
        "f8c5",
        "g1f3",
        "c8e6",
        "e1g1",
        "b8c6",
        "c1g5",
        "d8d7",
        "f1e1",
        "h7h6",
        "g5f4",
        "g8f6",
        "d3b5",
        "e8g8",
    ):
        board.push_uci(move)

    resolution = resolve(
        normalize("ты делаешь короткую рокировку ладненько разменяемся слон бьет на c 6 b 5 c 6"),
        board,
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == "b5c6"


def test_multiple_legal_moves_in_one_utterance_are_never_silently_narrowed() -> None:
    resolution = resolve(normalize("е 2 е 4 и д 2 д 4"), chess.Board())

    assert resolution.status is ResolutionStatus.AMBIGUOUS
    assert set(resolution.candidates) == {"e2e4", "d2d4"}


def test_two_knights_on_one_square_stay_ambiguous() -> None:
    board = chess.Board(TWO_KNIGHTS_FEN)

    resolution = resolve(normalize("конь дэ два"), board)

    assert resolution.status is ResolutionStatus.AMBIGUOUS
    assert set(resolution.candidates) == {"b1d2", "f3d2"}
    assert resolution.move is None


def test_naming_the_source_file_disambiguates_the_knights() -> None:
    board = chess.Board(TWO_KNIGHTS_FEN)

    resolution = resolve(normalize("конь бэ дэ два"), board)

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == "b1d2"


def test_a_weak_source_file_before_the_destination_disambiguates_safely() -> None:
    board = chess.Board(TWO_KNIGHTS_FEN)

    resolution = resolve(normalize("конь ф дэ два"), board)

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == "f3d2"


def test_two_rooks_on_one_square_stay_ambiguous() -> None:
    board = chess.Board(TWO_ROOKS_FEN)

    resolution = resolve(normalize("ладья дэ один"), board)

    assert resolution.status is ResolutionStatus.AMBIGUOUS
    assert set(resolution.candidates) == {"a1d1", "h1d1"}


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("рокировка", "e1g1"),
        ("короткая рокировка", "e1g1"),
        ("длинная рокировка", "e1c1"),
        ("рокировка в длинную сторону", "e1c1"),
        ("большая рокировка", "e1c1"),
        # A polite aside must not castle to the other side.
        ("рокировка, большое спасибо", "e1g1"),
        ("рокировка не длинная", "e1g1"),
        ("0-0", "e1g1"),
        ("00", "e1g1"),
        ("0 тире 0", "e1g1"),
        ("рокирую в короткую сторону", "e1g1"),
        ("0-0-0", "e1c1"),
    ],
)
def test_resolves_castling(utterance: str, expected: str) -> None:
    board = chess.Board(CASTLING_FEN)

    resolution = resolve(normalize(utterance), board)

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == expected


def test_promotion_without_a_named_piece_is_ambiguous() -> None:
    board = chess.Board(PROMOTION_FEN)

    resolution = resolve(normalize("пешка а семь а восемь"), board)

    assert resolution.status is ResolutionStatus.AMBIGUOUS
    assert set(resolution.candidates) == {"a7a8q", "a7a8r", "a7a8b", "a7a8n"}


def test_promotion_piece_is_taken_from_the_utterance() -> None:
    board = chess.Board(PROMOTION_FEN)

    resolution = resolve(normalize("пешка а семь а восемь превращение в ферзя"), board)

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == "a7a8q"
    assert resolution.recognized.promotion == "q"


def test_a_king_or_pawn_is_not_accepted_as_a_promotion_piece() -> None:
    king = normalize("пешка а семь а восемь королем")
    pawn = normalize("пешка а семь а восемь пешкой")

    assert all(token.kind.name != "PROMOTION" for token in king.signature)
    assert all(token.kind.name != "PROMOTION" for token in pawn.signature)


def test_instrumental_piece_after_coordinates_is_not_a_promotion() -> None:
    normalized = normalize("c 5 d 3 конем")

    assert all(token.kind is not TokenKind.PROMOTION for token in normalized.signature)


def test_a_claimed_capture_must_be_a_real_capture() -> None:
    board = chess.Board(CAPTURE_FEN)

    capture = resolve(normalize("пешка е четыре берет дэ пять"), board)
    quiet = resolve(normalize("пешка е четыре берет е пять"), board)

    assert capture.status is ResolutionStatus.RESOLVED
    assert capture.move == "e4d5"
    assert quiet.status is ResolutionStatus.UNMATCHED


def test_unmatched_utterance_keeps_the_recognised_parts() -> None:
    resolution = resolve(normalize("конь е два е пять"), chess.Board())

    assert resolution.status is ResolutionStatus.UNMATCHED
    assert resolution.move is None
    assert resolution.recognized.piece == "N"
    assert resolution.recognized.source == "e2"
    assert resolution.recognized.destination == "e5"


def test_three_spoken_squares_are_not_reinterpreted_by_dropping_the_middle_one() -> None:
    resolution = resolve(normalize("е два е три е четыре"), chess.Board())

    assert resolution.status is ResolutionStatus.UNMATCHED
    assert resolution.recognized.source is None
    assert resolution.recognized.destination is None


def test_utterance_without_move_tokens_is_unmatched() -> None:
    normalized = normalize("сегодня хорошая погода")

    resolution = resolve(normalized, chess.Board())

    assert resolution.status is ResolutionStatus.UNMATCHED
    assert resolution.recognized.is_empty


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("сдаюсь", CommandKind.RESIGN),
        ("я сдаюсь", CommandKind.RESIGN),
        ("новая партия", CommandKind.NEW_GAME),
        ("давай сначала", CommandKind.NEW_GAME),
        ("отмени ход", CommandKind.UNDO),
        ("предлагаю ничью", CommandKind.CLAIM_DRAW),
        ("продолжаем", CommandKind.CONTINUE),
        ("что ты умеешь", CommandKind.HELP),
        ("что ты умеешь делать", CommandKind.HELP),
        ("что ты можешь делать", CommandKind.HELP),
        ("какой уровень сложности", CommandKind.LEVEL_QUERY),
        ("какая позиция", CommandKind.POSITION_QUERY),
        ("где стоит мой король", CommandKind.POSITION_QUERY),
        ("где белые слоны", CommandKind.POSITION_QUERY),
        ("какой был последний ход", CommandKind.POSITION_QUERY),
        ("какой был последний ход черных", CommandKind.POSITION_QUERY),
        ("что сделали черные четыре хода назад", CommandKind.POSITION_QUERY),
        ("что было четыре хода назад", CommandKind.POSITION_QUERY),
        ("какой был второй ход черных", CommandKind.POSITION_QUERY),
        ("кто стоит на седьмой горизонтали", CommandKind.POSITION_QUERY),
        # A rank named as a term or a rule, with no number to read.
        ("что такое мат по последней горизонтали", CommandKind.UNKNOWN),
        # A rank and a move number named inside a question about the rules.
        ("может ли пешка превратиться на восьмой горизонтали", CommandKind.HELP),
        ("как сделать первый ход", CommandKind.HELP),
        # Asking the coach what to play, not asking what stands where.
        ("что стоит сыграть", CommandKind.UNKNOWN),
        ("назови второй полный ход", CommandKind.POSITION_QUERY),
        ("какой был ход номер два", CommandKind.POSITION_QUERY),
        ("какой был одиннадцатый ход", CommandKind.POSITION_QUERY),
        ("ход назад", CommandKind.UNDO),
        ("чей ход", CommandKind.POSITION_QUERY),
        ("есть ли шах сейчас", CommandKind.POSITION_QUERY),
        ("что ты услышал", CommandKind.REPEAT_HEARD),
        ("повтори медленно", CommandKind.REPEAT_SLOW),
    ],
)
def test_control_commands_are_separated_before_move_resolution(utterance: str, expected: CommandKind) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is expected
    assert routed.move is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("какая расстановка сейчас", CommandKind.POSITION_QUERY),
        ("назови еще раз свой ход", CommandKind.POSITION_QUERY),
        ("откати прошлые два хода", CommandKind.UNDO),
        ("какой ход ты посоветуешь", CommandKind.TRAINING),
        ("разбор", CommandKind.REVIEW),
        ("сменить цвет", CommandKind.REMATCH),
        ("следующая игра за черных", CommandKind.REMATCH),
        ("выключи навык", CommandKind.EXIT),
    ],
)
def test_production_command_phrases_are_routed(utterance: str, expected: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is expected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        # Alice requires the bare stop words to close the skill on the spot.
        ("выход", CommandKind.EXIT),
        ("выйти", CommandKind.EXIT),
        ("стоп", CommandKind.EXIT),
        ("выход из игры", CommandKind.EXIT),
        ("выйти из шахмат", CommandKind.EXIT),
        # Named in passing: understood, but asked about before the skill closes.
        ("выход пожалуйста", CommandKind.EXIT_CONFIRM),
        ("юра выход", CommandKind.EXIT_CONFIRM),
        ("давай выход", CommandKind.EXIT_CONFIRM),
        ("выход отсюда", CommandKind.EXIT_CONFIRM),
        ("я хочу выйти", CommandKind.EXIT_CONFIRM),
        ("я выхожу", CommandKind.EXIT_CONFIRM),
        # A developing move, never a request to leave.
        ("выход коня на е пять", CommandKind.CLARIFY),
        ("выход белого коня", CommandKind.CLARIFY),
        ("выход ферзя", CommandKind.CLARIFY),
        # Puzzles keep owning their own exit.
        ("выход из задачи", CommandKind.PUZZLE),
    ],
)
def test_leaving_is_commanded_outright_or_asked_about(utterance: str, expected: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is expected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("убери навык", CommandKind.EXIT),
        ("выйти из партии", CommandKind.EXIT),
        ("не хочу играть", CommandKind.EXIT),
        ("выключи шахматы", CommandKind.EXIT),
        ("выключись", CommandKind.EXIT),
        ("выключи мне юру", CommandKind.EXIT),
        ("как тебя выключить а", CommandKind.EXIT),
        ("пауза", CommandKind.PAUSE),
        ("замолчи", CommandKind.EXIT),
        ("алиса выйти", CommandKind.EXIT),
        ("я сдался", CommandKind.RESIGN),
        ("закончи партию", CommandKind.RESIGN),
        ("игра окончена", CommandKind.RESIGN),
        ("давай поиграем в шахматы", CommandKind.START),
        ("алиса твой ход", CommandKind.CONTINUE),
        ("да давай продолжим", CommandKind.CONTINUE),
        ("повтори свой ход", CommandKind.POSITION_QUERY),
        ("повтори ход", CommandKind.POSITION_QUERY),
        ("повтори еще раз свой ход", CommandKind.POSITION_QUERY),
        ("оценка позиции", CommandKind.TRAINING),
        ("включи режим трения", CommandKind.TRAINING),
        ("выключи стримеры", CommandKind.TRAINING),
        ("продиктуй всю партию", CommandKind.REVIEW),
        ("сделай разбор всей партии", CommandKind.REVIEW),
        ("задача", CommandKind.PUZZLE),
        ("задачи", CommandKind.PUZZLE),
        ("можно мне за черных", CommandKind.NEW_GAME),
        ("другим цветом черный", CommandKind.REMATCH),
        ("говори обычно", CommandKind.PREFERENCE),
        ("отключись", CommandKind.EXIT),
        ("алиса убрать шахматы", CommandKind.EXIT),
        ("шахматы убрать", CommandKind.EXIT),
        ("сейчас я расставлю фигуры", CommandKind.BOARD_SETUP),
        ("алиса", CommandKind.ATTENTION),
        ("доброе утро", CommandKind.SOCIAL),
        ("ты тут", CommandKind.SOCIAL),
        ("понятно", CommandKind.BACKCHANNEL),
        ("угу", CommandKind.BACKCHANNEL),
        ("повтори", CommandKind.REPEAT_REPLY),
        ("подожди", CommandKind.PAUSE),
        ("я еще поле выставляю", CommandKind.PAUSE),
        ("ход", CommandKind.AMBIGUOUS_TURN),
        ("почему", CommandKind.WHY),
        ("не знаю", CommandKind.DONT_KNOW),
        ("что мне делать", CommandKind.HELP),
        ("как сделать ход", CommandKind.HELP),
        ("хорошие годы", CommandKind.TRAINING),
    ],
)
def test_observed_production_phrases_are_routed(utterance: str, expected: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is expected


def test_unrelated_continue_phrase_is_not_a_chess_command() -> None:
    assert route("алиса продолжай трек", chess.Board()).kind is CommandKind.UNKNOWN


def test_compound_confirmation_stays_explicit_and_does_not_capture_another_request() -> None:
    assert confirmation_answer("да подтверждаю") is True
    assert confirmation_answer("да просто песню поставь") is None


@pytest.mark.parametrize(
    "utterance",
    [
        "включи музыку",
        "поставь мне песню пожалуйста",
        "расскажи сказку про шахматы",
        "расскажи погоду",
        "прогноз погоды на сегодня",
    ],
)
def test_platform_requests_are_handed_back_before_move_resolution(utterance: str) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.PLATFORM
    assert routed.move is None
    assert routed.resolution is None


def test_negated_exit_is_not_claimed_as_a_command() -> None:
    assert route("не отключись", chess.Board()).kind is CommandKind.UNKNOWN


@pytest.mark.parametrize("utterance", ["поехали", "погнали", "начали", "начинаем"])
def test_start_like_phrases_only_confirm_a_pending_continuation(utterance: str) -> None:
    assert confirmation_answer(utterance, CommandKind.CONTINUE) is True
    assert confirmation_answer(utterance, CommandKind.RESIGN) is None


@pytest.mark.parametrize("utterance", ["угу", "конечно", "давай", "да давай"])
def test_friendly_answers_confirm_safe_pending_actions_but_not_resignation(utterance: str) -> None:
    assert confirmation_answer(utterance, CommandKind.CONTINUE) is True
    assert confirmation_answer(utterance, CommandKind.RESIGN) is None


@pytest.mark.parametrize("utterance", ["нет", "не надо", "давай не будем", "отмена"])
def test_natural_negative_answers_cancel_any_pending_action(utterance: str) -> None:
    assert confirmation_answer(utterance, CommandKind.CONTINUE) is False


def test_colloquial_knight_name_is_still_resolved_only_against_legal_moves() -> None:
    routed = route("лошадь эф три", chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "g1f3"


def test_incomplete_and_compound_moves_require_clarification() -> None:
    assert route("я конем хожу", chess.Board()).kind is CommandKind.CLARIFY
    assert route("мой ход", chess.Board()).kind is CommandKind.CLARIFY
    assert route("е 2 е 4 е 7 е 5", chess.Board()).kind is CommandKind.CLARIFY
    assert route("конь эф три слон цэ четыре", chess.Board()).kind is CommandKind.CLARIFY
    assert route("рокировка потом конь эф три", chess.Board()).kind is CommandKind.CLARIFY


def test_undo_command_carries_the_requested_full_move_count() -> None:
    routed = route("откати прошлые два полных хода", chess.Board())

    assert routed.kind is CommandKind.UNDO
    assert routed.undo_count == 2


def test_occupied_destination_is_explained_for_a_piece_with_one_geometric_source() -> None:
    board = chess.Board()
    for move in ("a2a3", "g8f6", "d2d4", "g7g6"):
        board.push_uci(move)

    routed = route("конь а 3", board)

    assert routed.kind is CommandKind.ILLEGAL_MOVE
    assert routed.explanation is not None
    assert "занято вашей фигурой" in routed.explanation.text


def test_repeat_heard_answers_with_the_previous_utterance() -> None:
    routed = route("что ты услышал", chess.Board(), last_heard="пешка е два е четыре")

    assert routed.kind is CommandKind.REPEAT_HEARD
    assert routed.heard == "пешка е два е четыре"


def test_router_plays_a_confident_move() -> None:
    routed = route("пешка е два е четыре", chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"
    assert routed.clarification is None


def test_router_asks_instead_of_guessing_between_candidates() -> None:
    board = chess.Board(TWO_KNIGHTS_FEN)

    routed = route("конь дэ два", board)

    assert routed.kind is CommandKind.CLARIFY
    assert routed.move is None
    assert routed.clarification is not None
    assert set(routed.clarification.candidates) == {"b1d2", "f3d2"}


def test_router_asks_when_confidence_is_below_the_threshold() -> None:
    routed = route("е четыре", chess.Board(), confidence_threshold=0.9)

    assert routed.kind is CommandKind.CLARIFY
    assert routed.move is None


def test_yes_confirms_a_single_candidate() -> None:
    pending = PendingClarification(heard="пешка е два е четыре", candidates=("e2e4",))

    routed = route("да", chess.Board(), pending=pending)

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"
    assert routed.clarification is None


def test_yes_never_picks_one_of_several_candidates() -> None:
    board = chess.Board(TWO_KNIGHTS_FEN)
    pending = PendingClarification(heard="конь дэ два", candidates=("b1d2", "f3d2"))

    routed = route("да", board, pending=pending)

    assert routed.kind is CommandKind.CLARIFY
    assert routed.move is None
    assert routed.clarification == pending


def test_no_cancels_the_clarification() -> None:
    pending = PendingClarification(heard="пешка е два е четыре", candidates=("e2e4",))

    routed = route("нет", chess.Board(), pending=pending)

    assert routed.kind is CommandKind.CANCEL_CLARIFY
    assert routed.clarification is None


def test_a_fuller_phrasing_answers_the_clarification() -> None:
    board = chess.Board(TWO_KNIGHTS_FEN)
    pending = PendingClarification(heard="конь дэ два", candidates=("b1d2", "f3d2"))

    routed = route("конь эф три дэ два", board, pending=pending)

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "f3d2"


def test_without_a_game_no_move_is_resolved() -> None:
    routed = route("пешка е два е четыре")

    assert routed.kind is CommandKind.UNKNOWN
    assert routed.move is None


def test_normalisation_keeps_no_original_casing_or_punctuation() -> None:
    normalized = normalize("Пешка Е-два, на Е четыре!")

    assert normalized.text == "пешка е два на е четыре"


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("справка", CommandKind.HELP),
        ("помощь", CommandKind.HELP),
        ("справка по позиции", CommandKind.HELP),
        ("все команды", CommandKind.HELP),
        ("список команд", CommandKind.HELP),
        ("как играть", CommandKind.HELP),
        ("выйти из справки", CommandKind.HELP_EXIT),
        ("закрой справку", CommandKind.HELP_EXIT),
        ("хватит справки", CommandKind.HELP_EXIT),
    ],
)
def test_help_commands_never_reach_move_resolution(utterance: str, expected: CommandKind) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is expected
    assert routed.move is None
    assert routed.resolution is None


@pytest.mark.parametrize(
    "utterance",
    [
        "правила шахмат",
        "правила игры в шахматы",
        "основы шахмат",
        "расскажи правила шахмат",
        "объясни правила игры в шахматы",
        "расскажи о правилах шахмат",
        "расскажи про правила игры в шахматы",
        "как правильно играть в шахматы",
        "объясни как играть в шахматы",
        "научи меня играть в шахматы",
        "я хочу научиться играть в шахматы",
        "я не умею играть в шахматы",
        "какие правила в шахматах",
    ],
)
def test_general_chess_rule_questions_open_help(utterance: str) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.HELP
    assert routed.move is None
    assert routed.resolution is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("давай играть в шахматы", CommandKind.START),
        ("я умею играть в шахматы", CommandKind.UNKNOWN),
        ("почему я плохо играю в шахматы", CommandKind.UNKNOWN),
        ("включи правила дорожного движения", CommandKind.UNKNOWN),
        ("поставь музыку про шахматы", CommandKind.PLATFORM),
    ],
)
def test_rules_help_does_not_capture_other_intents(utterance: str, expected: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is expected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("почему ты так сходил", TrainingQuestion.WHY_MOVE),
        ("что ты задумал", TrainingQuestion.THREAT),
    ],
)
def test_male_trainer_phrases_are_routed(utterance: str, expected: TrainingQuestion) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.TRAINING
    assert routed.training is not None
    assert routed.training.question is expected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        # Yandex ASR keeps «хорошие» and invents the noun; each of these is a real transcript.
        ("хорошие коды", TrainingQuestion.CANDIDATES),
        ("хорошие коты", TrainingQuestion.CANDIDATES),
        ("хорошие сады", TrainingQuestion.CANDIDATES),
        ("хорошие годы", TrainingQuestion.CANDIDATES),
        ("хорошие ходи", TrainingQuestion.CANDIDATES),
        ("хорошие хиты", TrainingQuestion.CANDIDATES),
        ("хороший садик", TrainingQuestion.CANDIDATES),
        ("какие коды лучше", TrainingQuestion.CANDIDATES),
        ("какой ход лучше", TrainingQuestion.CANDIDATES),
        ("какой ход лучший", TrainingQuestion.CANDIDATES),
        ("хороший совет", TrainingQuestion.HINT),
        ("чем ты мне угрожаешь", TrainingQuestion.THREAT),
        ("кем ты угрожаешь", TrainingQuestion.THREAT),
        ("что угрожает моему королю", TrainingQuestion.THREAT),
        ("какие угрозы", TrainingQuestion.THREAT),
    ],
)
def test_the_manglings_of_a_trainer_question_reach_the_trainer(utterance: str, expected: TrainingQuestion) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.TRAINING
    assert routed.training is not None
    assert routed.training.question is expected


@pytest.mark.parametrize(
    "utterance",
    [
        "хорошо играю",
        "все хорошо",
        "включи хорошую песню",
        "поставь хорошую песню",
        "хорошая защита",
        "хорошая жертва",
        "хороший размен",
    ],
)
def test_a_judgement_or_an_off_domain_wish_is_not_a_trainer_question(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.TRAINING


@pytest.mark.parametrize(
    "utterance",
    ["конь эф три угрожает матом", "пешка е два е четыре угрожает", "включи песню угроза", "я угрожаю ферзем"],
)
def test_a_threat_named_inside_another_command_does_not_become_a_trainer_question(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.TRAINING


@pytest.mark.parametrize(
    "utterance",
    ["прошу помощи", "помощь нужна", "попросить помощи", "просим помощи", "попросите помощи", "помогите помогите"],
)
def test_asking_for_help_in_the_genitive_still_opens_help(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.HELP


@pytest.mark.parametrize(
    "utterance",
    [
        "короткая рокировка",
        "рокировка короткая",
        "рокирую в короткую сторону",
        "мой ход короткая рокировка",
        "говори короткая рокировка",
    ],
)
def test_castling_is_never_read_as_a_notation_or_brevity_setting(utterance: str) -> None:
    assert route(utterance, chess.Board(CASTLING_FEN)).kind is not CommandKind.PREFERENCE


@pytest.mark.parametrize("utterance", ["покажи нотацию", "партию в нотации"])
def test_asking_for_the_written_game_is_not_a_notation_setting(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.REVIEW


@pytest.mark.parametrize(
    "utterance",
    ["покажи полную нотацию", "что такое полная нотация", "какая сейчас нотация", "что значит короткая нотация"],
)
def test_asking_about_a_setting_never_changes_it(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.PREFERENCE


@pytest.mark.parametrize("utterance", ["аннотация", "короткая", "развернуть ход"])
def test_a_word_that_cannot_name_a_style_changes_no_setting(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.PREFERENCE


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("мои ошибки", ReviewQuestion.SUMMARY),
        ("какие у меня ошибки", ReviewQuestion.SUMMARY),
        ("какие мои ошибки", ReviewQuestion.SUMMARY),
        ("покажи мои ошибки", ReviewQuestion.SUMMARY),
        ("какая моя ошибка", ReviewQuestion.MAIN_MISTAKE),
        ("сколько было ошибок", ReviewQuestion.MISTAKE_COUNT),
        ("сколько у меня было ошибок", ReviewQuestion.MISTAKE_COUNT),
        ("сколько я раз ошибся", ReviewQuestion.MISTAKE_COUNT),
        ("разобрать партию", ReviewQuestion.SUMMARY),
        ("разобрать игру", ReviewQuestion.SUMMARY),
        ("а разобрать игру", ReviewQuestion.SUMMARY),
        ("разобрать", ReviewQuestion.SUMMARY),
        ("разбор партий", ReviewQuestion.SUMMARY),
        ("алиса разбор", ReviewQuestion.SUMMARY),
        ("разбор разбор", ReviewQuestion.SUMMARY),
        ("последняя партия", ReviewQuestion.SUMMARY),
        ("вернуться к предыдущей партии", ReviewQuestion.SUMMARY),
    ],
)
def test_a_question_about_the_finished_game_reaches_the_review(
    utterance: str,
    expected: ReviewQuestion,
) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.REVIEW
    assert routed.review is not None
    assert routed.review.question is expected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("где я ошибся", CommandKind.TRAINING),
        ("в чем моя ошибка", CommandKind.TRAINING),
        ("сколько ходов мы сыграли", CommandKind.GAME_FACT),
        ("сколько пешек стоила ошибка на пятом ходу", CommandKind.UNKNOWN),
        ("продолжи последнюю партию", CommandKind.CONTINUE),
    ],
)
def test_a_question_about_the_running_game_is_not_a_review(utterance: str, expected: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is expected


@pytest.mark.parametrize(
    "utterance",
    [
        "что ты разобрала",
        "разобрать сколько ходов мы сыграли",
        "разобрать почему ты так сходила",
        "разобрать ход конь эф три",
        "алиса разборчиво",
    ],
)
def test_a_sentence_that_merely_contains_the_word_is_not_a_review_request(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.REVIEW


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("алис на полный экран я не вижу", ScreenWish.BIGGER),
        ("алиса можешь увеличить шахматную доску", ScreenWish.BIGGER),
        ("а ты не мог бы эту свою увеличить доску", ScreenWish.BIGGER),
        ("алиса доску на полный экран", ScreenWish.BIGGER),
        ("сделай больше экран", ScreenWish.BIGGER),
        ("увеличь картинку", ScreenWish.BIGGER),
        ("сделай картинку больше", ScreenWish.BIGGER),
        ("увеличь вижу плохо", ScreenWish.BIGGER),
        ("извини я не вижу не фига очень маленькие символы", ScreenWish.BIGGER),
        ("да я не вижу букв никаких", ScreenWish.BIGGER),
        ("алиса а можно играть не голосом а визуально", ScreenWish.TAP),
        ("да визуально поставьте я не могу так", ScreenWish.TAP),
        ("я не могу так чтобы я могу только визуально", ScreenWish.TAP),
        ("доску мне дайте пальчиком я хочу ходить а не голосовым", ScreenWish.TAP),
    ],
)
def test_a_request_about_the_picture_is_answered_about_the_picture(
    utterance: str,
    expected: ScreenWish,
) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.SCREEN
    assert routed.screen is not None
    assert routed.screen.wish is expected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("не вижу шаха", CommandKind.UNKNOWN),
        ("не вижу куда ходить", CommandKind.UNKNOWN),
        ("не вижу шаха покажи доску", CommandKind.POSITION_QUERY),
        ("я буквально не вижу хорошего хода", CommandKind.TRAINING),
        ("как увеличивается шахматный рейтинг", CommandKind.UNKNOWN),
        ("как увеличить шахматный рейтинг", CommandKind.UNKNOWN),
        ("я увеличу экран и посмотрю", CommandKind.UNKNOWN),
        ("у меня болит пальчик", CommandKind.UNKNOWN),
        ("алиса покажи поле на экране", CommandKind.POSITION_QUERY),
        ("алиса покажи мне шахматное поле на экране", CommandKind.POSITION_QUERY),
        ("я играл визуально теперь хочу голосом", CommandKind.UNKNOWN),
        ("веду пальцем по доске прочитай позицию", CommandKind.POSITION_QUERY),
        ("покажи доску", CommandKind.POSITION_QUERY),
        ("где поле я ничего не вижу", CommandKind.POSITION_QUERY),
        ("а картинка где", CommandKind.POSITION_QUERY),
    ],
)
def test_not_seeing_something_on_the_board_is_not_a_request_about_the_screen(
    utterance: str,
    expected: CommandKind,
) -> None:
    assert route(utterance, chess.Board()).kind is expected


def test_help_navigation_is_matched_before_the_new_game_command() -> None:
    assert route("справка сначала", chess.Board()).kind is CommandKind.HELP


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("говори кратко", PreferenceChange(detail_level=DetailLevel.BRIEF)),
        ("отвечай подробнее", PreferenceChange(detail_level=DetailLevel.DETAILED)),
        ("обычная подробность ответов", PreferenceChange(detail_level=DetailLevel.NORMAL)),
        ("говори медленнее", PreferenceChange(pause_style=PauseStyle.EXTENDED)),
        ("говори быстрее", PreferenceChange(pause_style=PauseStyle.NORMAL)),
        ("называй только клетку назначения", PreferenceChange(notation_style=NotationStyle.SHORT)),
        # Yandex ASR returns «аннотация» for «нотация», in any adjective ending.
        ("короткая аннотация", PreferenceChange(notation_style=NotationStyle.SHORT)),
        ("краткая аннотация", PreferenceChange(notation_style=NotationStyle.SHORT)),
        ("полная аннотация", PreferenceChange(notation_style=NotationStyle.FULL)),
        ("полное аннотация", PreferenceChange(notation_style=NotationStyle.FULL)),
        ("включить полную аннотацию", PreferenceChange(notation_style=NotationStyle.FULL)),
        ("полное нотация", PreferenceChange(notation_style=NotationStyle.FULL)),
        ("говори коротко", PreferenceChange(detail_level=DetailLevel.BRIEF)),
        ("говори краткую нотацию", PreferenceChange(notation_style=NotationStyle.SHORT)),
        ("полная нотация", PreferenceChange(notation_style=NotationStyle.FULL)),
        ("доску всегда белыми", PreferenceChange(board_orientation=BoardOrientation.WHITE)),
        ("ориентация за черных", PreferenceChange(board_orientation=BoardOrientation.BLACK)),
        ("доска по моему цвету", PreferenceChange(board_orientation=BoardOrientation.PLAYER)),
        ("выключи игровые звуки", PreferenceChange(sounds_enabled=False)),
        ("играем со звуками", PreferenceChange(sounds_enabled=True)),
    ],
)
def test_settings_commands_never_reach_move_resolution(utterance: str, expected: PreferenceChange) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.PREFERENCE
    assert routed.preference == expected
    assert routed.move is None
    assert routed.resolution is None


@pytest.mark.parametrize(
    "utterance",
    ["что на доске у черных", "где белые слоны", "повтори медленно", "какая сложность"],
)
def test_questions_are_not_mistaken_for_settings(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.PREFERENCE


@pytest.mark.parametrize(
    ("utterance", "enabled"),
    [
        ("отключи музыкальное сопровождение", False),
        ("можно ли выключить звуки", False),
        ("убери звуковые сигналы", False),
        ("выключи мне пожалуйста звуки", False),
        ("не надо звуков", False),
        ("звуки мне не нужны", False),
        ("убери озвучку", False),
        ("играем в тишине", False),
        ("играй без звука", False),
        ("не включай звуки", False),
        ("не хочу включать звуки", False),
        ("не играй со звуком", False),
        ("не будем играть со звуком", False),
        ("можно включить звуки", True),
        ("верни музыкальное сопровождение", True),
        ("хочу звуки", True),
        ("играй со звуком", True),
        ("верни озвучку", True),
        ("не выключай звуки", True),
        ("не хочу выключать звуки", True),
        ("мне включи звуки", True),
    ],
)
def test_the_sound_switch_is_recognised_however_it_is_phrased(utterance: str, enabled: bool) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.PREFERENCE
    assert routed.preference == PreferenceChange(sounds_enabled=enabled)


@pytest.mark.parametrize("utterance", ["включи музыку", "выключи музыку", "поставь музыку"])
def test_a_request_for_real_music_stays_with_the_platform(utterance: str) -> None:
    """The skill has no music library; only its own сопровождение is a setting."""
    assert route(utterance, chess.Board()).kind is CommandKind.PLATFORM


@pytest.mark.parametrize(
    "utterance",
    [
        "что со звуком",
        "у меня проблема со звуком",
        "что с сигналом",
        "включи сигнализацию",
        "почему ты выключила звуки",
        "когда включаю звуки ничего не происходит",
    ],
)
def test_a_remark_about_sound_never_rewrites_the_setting(utterance: str) -> None:
    """A durable preference needs a request, not a mention of the word."""
    assert route(utterance, chess.Board()).kind is not CommandKind.PREFERENCE


def test_a_board_question_does_not_persist_orientation() -> None:
    assert route("где на доске черные слоны", chess.Board()).kind is CommandKind.POSITION_QUERY


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("реванш", RematchRequest()),
        ("еще одну партию", RematchRequest()),
        ("реванш другим цветом", RematchRequest(color=RematchColor.SWAP)),
        ("реванш черными", RematchRequest(color=RematchColor.BLACK)),
        ("еще партию белыми", RematchRequest(color=RematchColor.WHITE)),
        ("сыграем еще, только сложнее", RematchRequest(harder=True)),
        ("реванш другим цветом и потруднее", RematchRequest(color=RematchColor.SWAP, harder=True)),
    ],
)
def test_rematch_carries_the_colour_and_level_it_asks_for(utterance: str, expected: RematchRequest) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.REMATCH
    assert routed.rematch == expected
    assert routed.move is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("уровень пять", LevelRequest(LevelIntent.SET, 5)),
        ("поставь уровень 15", LevelRequest(LevelIntent.SET, 15)),
        ("уровень номер ноль", LevelRequest(LevelIntent.SET, 0)),
        ("нулевой уровень", LevelRequest(LevelIntent.SET, 0)),
        ("1 уровень", LevelRequest(LevelIntent.SET, 1)),
        ("пятнадцатый уровень", LevelRequest(LevelIntent.SET, 15)),
        ("сложность 0", LevelRequest(LevelIntent.SET, 0)),
        ("уровень сложности 2", LevelRequest(LevelIntent.SET, 2)),
        ("можно уровень пять", LevelRequest(LevelIntent.SET, 5)),
        ("можно мне пятый уровень", LevelRequest(LevelIntent.SET, 5)),
        ("уровень пять пожалуйста", LevelRequest(LevelIntent.SET, 5)),
        ("поставь уровень на пять", LevelRequest(LevelIntent.SET, 5)),
        ("установи сложность на десять", LevelRequest(LevelIntent.SET, 10)),
        ("изменить уровень на пять", LevelRequest(LevelIntent.SET, 5)),
        ("поменяй на пятый уровень", LevelRequest(LevelIntent.SET, 5)),
        ("давайте поставим уровень пять", LevelRequest(LevelIntent.SET, 5)),
        ("прошу поставить уровень пять", LevelRequest(LevelIntent.SET, 5)),
        ("поставь уровень игры пять", LevelRequest(LevelIntent.SET, 5)),
        ("переключись на уровень пять", LevelRequest(LevelIntent.SET, 5)),
        ("могу я изменить уровень на пять", LevelRequest(LevelIntent.SET, 5)),
        ("уровень нуль", LevelRequest(LevelIntent.SET, 0)),
        ("уровень пятьдесят", LevelRequest(LevelIntent.SET, 20)),
        ("уровень 100", LevelRequest(LevelIntent.SET, 20)),
        ("уровень сто", LevelRequest(LevelIntent.CAPABILITY)),
        ("снизь уровень", LevelRequest(LevelIntent.CAPABILITY)),
        ("а уровень 5 как сделать", LevelRequest(LevelIntent.CAPABILITY)),
        ("уровень выше", LevelRequest(LevelIntent.CAPABILITY)),
        ("15 уровень на шахматах это высокий или низкий", LevelRequest(LevelIntent.SCALE)),
        ("сколько всего уровней", LevelRequest(LevelIntent.SCALE)),
        ("какой самый сильный уровень", LevelRequest(LevelIntent.SCALE)),
        ("что значит пятый уровень", LevelRequest(LevelIntent.SCALE)),
        ("почему у меня пятый уровень", LevelRequest(LevelIntent.SCALE)),
        ("пятый уровень это сложно", LevelRequest(LevelIntent.SCALE)),
        ("на пятом уровне я сильный", LevelRequest(LevelIntent.SCALE)),
    ],
)
def test_a_level_command_carries_the_difficulty_and_never_mutates_on_a_question(
    utterance: str,
    expected: LevelRequest,
) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.LEVEL
    assert routed.level == expected
    assert routed.move is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("новая игра уровень 5", CommandKind.NEW_GAME),
        ("новая партия белыми уровень 4", CommandKind.NEW_GAME),
        ("алиса давай играть в нулевой уровень", CommandKind.START),
        ("сыграем еще, только сложнее", CommandKind.REMATCH),
        ("какой сейчас уровень", CommandKind.LEVEL_QUERY),
        ("поменять уровень", CommandKind.LEVEL_QUERY),
        ("изменить уровень", CommandKind.LEVEL_QUERY),
        ("на каком уровне", CommandKind.LEVEL_QUERY),
    ],
)
def test_naming_a_level_never_takes_a_phrase_from_the_commands_that_own_it(
    utterance: str,
    expected: CommandKind,
) -> None:
    assert route(utterance, chess.Board()).kind is expected


@pytest.mark.parametrize(
    "utterance",
    ["игра белыми уровень 5", "играть партию 1 уровень", "хочу играть пятый уровень", "поиграем уровень 3"],
)
def test_a_level_named_with_a_game_to_start_is_not_a_change_to_the_running_one(utterance: str) -> None:
    routed = route(utterance, chess.Board())

    assert route(utterance).kind is CommandKind.UNKNOWN
    assert routed.kind is not CommandKind.LEVEL
    assert routed.level is None


@pytest.mark.parametrize("utterance", ["поставь уровень громкости пять", "уровень звука пять", "уровень заряда"])
def test_a_level_of_something_other_than_chess_is_not_a_difficulty(utterance: str) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is not CommandKind.LEVEL
    assert routed.level is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("я играю черными", RematchColor.BLACK),
        ("эту партию играю чёрными", RematchColor.BLACK),
        ("давай белыми", RematchColor.WHITE),
        ("я хочу играть белыми", RematchColor.WHITE),
        ("можно мне чёрными", RematchColor.BLACK),
        ("буду играть белыми", RematchColor.WHITE),
        ("чёрными играю", RematchColor.BLACK),
        ("я играю за белых", RematchColor.WHITE),
        ("сыграем за чёрных", RematchColor.BLACK),
        # A refusal followed by a request still asks for the colour requested.
        ("не хочу белыми, давай черными", RematchColor.BLACK),
        # The skill is addressed by name, not handed the colour.
        ("юра давай белыми", RematchColor.WHITE),
    ],
)
def test_naming_a_colour_asks_to_play_it(utterance: str, expected: RematchColor) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.COLOR_CHOICE
    assert routed.rematch == RematchRequest(color=expected)
    assert routed.move is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("какие фигуры у чёрных", CommandKind.POSITION_QUERY),
        ("что сделали белые", CommandKind.POSITION_QUERY),
        ("покажи доску за белых", CommandKind.PREFERENCE),
        ("хочу играть за белых", CommandKind.NEW_GAME),
        ("каким цветом я играю", CommandKind.GAME_FACT),
        ("я белые или черные", CommandKind.GAME_FACT),
        ("ты играешь черными", CommandKind.BOARD_SETUP),
        ("реванш черными", CommandKind.REMATCH),
        # A question about the rules, not a request to be dealt that colour.
        ("можно ходить только белыми фигурами", CommandKind.UNKNOWN),
        # A refusal and a question about the choice both name a colour without asking for it.
        ("я не буду играть черными", CommandKind.UNKNOWN),
        ("мне играть белыми или черными", CommandKind.UNKNOWN),
        # The colour is handed to the engine, so it says nothing about the player's own.
        ("ты будешь играть черными", CommandKind.UNKNOWN),
        ("хочу чтобы ты играл черными", CommandKind.UNKNOWN),
        ("хочу чтобы юра играл черными", CommandKind.UNKNOWN),
        # Asking what a side may do, or how it is best played, starts nothing.
        ("можно черными брать на проходе", CommandKind.UNKNOWN),
        ("как лучше играть белыми", CommandKind.UNKNOWN),
    ],
)
def test_a_colour_named_in_another_case_keeps_its_own_command(utterance: str, expected: CommandKind) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is expected


def test_an_explicit_retraction_plays_the_move_that_followed_it() -> None:
    board = chess.Board()

    routed = route("слон эф один цэ четыре, ой нет пешка е два е четыре", board)

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"
    assert routed.clarification is None


@pytest.mark.parametrize("retracted", ["покажи доску", "сдаюсь", "отмени ход"])
def test_a_retraction_takes_back_a_command_as_well_as_a_move(retracted: str) -> None:
    routed = route(f"{retracted}, ой нет, пешка е два е четыре", chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"


@pytest.mark.parametrize("utterance", ["отмени ход, ой нет", "сдаюсь, ой нет, не надо"])
def test_a_command_taken_back_with_nothing_in_its_place_is_not_carried_out(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.UNKNOWN


def test_an_ordinary_negation_is_not_read_as_a_retraction() -> None:
    # Bare «нет» retracts only after a pause; here it merely negates.
    routed = route("в справке нет команды отмени ход", chess.Board())

    assert routed.kind is not CommandKind.UNDO


def test_a_move_number_in_the_instrumental_announces_a_move_instead_of_asking_for_one() -> None:
    routed = route("первым ходом пойду е два е четыре", chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"


def test_a_move_that_merely_says_the_word_move_is_still_a_move() -> None:
    # «ходом» is filler here, not the move-by-number question the router also matches.
    routed = route("пешка е два ходом на е четыре", chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"


def test_a_retraction_with_nothing_after_it_never_plays_the_move_it_took_back() -> None:
    board = chess.Board()

    routed = route("пешка е два е четыре, ой нет", board)

    assert routed.kind is CommandKind.CLARIFY
    assert routed.move is None
    assert routed.clarification is not None
    assert routed.clarification.candidates == ()


def test_a_bare_refusal_is_still_not_a_move_at_all() -> None:
    assert route("ой нет", chess.Board()).kind is CommandKind.UNKNOWN


def test_a_correction_without_a_retraction_is_only_offered_for_confirmation() -> None:
    board = chess.Board()

    routed = route("пешка дэ два дэ четыре, е два е четыре", board)

    assert routed.kind is CommandKind.CLARIFY
    assert routed.move is None
    assert routed.clarification is not None
    assert routed.clarification.candidates == ("e2e4",)


def test_a_confirmed_correction_becomes_the_move() -> None:
    board = chess.Board()
    pending = route("пешка дэ два дэ четыре, е два е четыре", board).clarification

    routed = route("да", board, pending=pending)

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"


def test_a_single_move_split_by_a_pause_is_still_played_outright() -> None:
    routed = route("пешка е два, е четыре", chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "e2e4"


def test_a_sequence_of_moves_is_still_refused_rather_than_corrected() -> None:
    routed = route("пешка е два е четыре потом конь жэ один эф три", chess.Board())

    assert routed.kind is CommandKind.CLARIFY
    assert routed.move is None
    assert routed.clarification is not None
    assert routed.clarification.candidates == ()


GLUED_RANKS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPQNPPP/RNB1KB1R w KQkq - 0 1"


def test_a_destination_file_swallowed_by_asr_still_plays_the_only_move_left() -> None:
    routed = route("пешка ц 24", chess.Board(GLUED_RANKS_FEN))

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "c2c4"


def test_a_swallowed_destination_file_is_asked_about_rather_than_guessed() -> None:
    routed = route("ферзь д 23", chess.Board(GLUED_RANKS_FEN))

    assert routed.kind is CommandKind.CLARIFY
    assert routed.move is None
    assert routed.clarification is not None
    assert set(routed.clarification.candidates) == {"d2c3", "d2d3", "d2e3"}


@pytest.mark.parametrize(
    "utterance",
    ["ферзь д 23", "пешка а 78 превращается в ферзя"],
)
def test_a_swallowed_destination_file_never_turns_the_source_into_the_destination(utterance: str) -> None:
    recognized = recognize(normalize(utterance).signature)

    assert recognized.source is not None
    assert recognized.destination is None


@pytest.mark.parametrize("utterance", ["ферзь д 23", "ферзь д 23 бьет", "ферзь д 23 пожалуйста"])
def test_a_source_square_the_piece_does_not_stand_on_is_never_played_as_a_destination(utterance: str) -> None:
    # The queen is on d1, so «ферзь д 23» names no move it can make: shortening
    # the reading to «ферзь д 2» would play Qd1-d2, which nobody asked for.
    # Whatever is said after the rank must not restore that shortened reading.
    routed = route(utterance, chess.Board("7k/8/8/8/8/8/8/K2Q4 w - - 0 1"))

    assert routed.kind is not CommandKind.MOVE
    assert routed.move is None


@pytest.mark.parametrize("piece", ["ферзь", "ладья", "слон", "конь"])
def test_a_promotion_survives_the_glued_rank_of_its_destination(piece: str) -> None:
    board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")

    resolution = resolve(normalize(f"пешка а 78 {piece}"), board)

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move is not None
    assert resolution.move.startswith("a7a8")


def test_politeness_after_the_promotion_piece_keeps_the_piece_that_was_named() -> None:
    board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")

    resolution = resolve(normalize("пешка а 78 ферзь пожалуйста"), board)

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.move == "a7a8q"


@pytest.mark.parametrize("utterance", ["мне 65 лет", "и 24", "а 24", "мне б 24", "же 24"])
def test_a_two_digit_number_outside_a_move_is_not_read_as_two_ranks(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.UNKNOWN


def test_a_two_digit_level_is_a_difficulty_and_never_two_ranks() -> None:
    routed = route("уровень 12", chess.Board())

    assert routed.kind is CommandKind.LEVEL
    assert routed.level == LevelRequest(LevelIntent.SET, 12)


def test_a_file_doubtful_before_a_glued_number_is_still_a_file_before_a_square() -> None:
    # Both knights reach d2; «б» is what tells the two of them apart.
    routed = route("конь б д два", chess.Board(TWO_KNIGHTS_FEN))

    assert routed.kind is CommandKind.MOVE
    assert routed.move == "b1d2"


@pytest.mark.parametrize("utterance", ["пешка е 43 бьет", "пешка е 43 бьет коня"])
def test_a_capture_claimed_after_the_recovered_rank_is_not_dropped_to_play_a_quiet_move(utterance: str) -> None:
    routed = route(utterance, chess.Board("4k3/8/8/8/4p3/8/8/4K3 b - - 0 1"))

    assert routed.kind is not CommandKind.MOVE
    assert routed.move is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("пешка е два е четыре повторяю четыре", "e2e4"),
        ("пешка е четыре повторяю четыре", "e2e4"),
        ("конь эф три повторяю три", "g1f3"),
    ],
)
def test_a_rank_repeated_after_a_move_is_an_echo_rather_than_a_destination(utterance: str, expected: str) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.MOVE
    assert routed.move == expected


@pytest.mark.parametrize(
    "utterance",
    [
        "завершить игру",
        "стоп игра",
        "хватит завершаем игру",
        "закончи заканчиваем шахматную партию",
        "конец игры",
        "конец партии",
        "игра закончена",
        "партия закончена",
        "игра завершена",
    ],
)
def test_asking_to_end_the_game_ends_the_game(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.RESIGN


@pytest.mark.parametrize(
    "utterance",
    [
        "помощь пожалуйста",
        "помогите пожалуйста",
        "помоги",
        "пожалуйста помоги",
        "алиса помоги",
        "помоги мне",
        "помоги пожалуйста мне",
    ],
)
def test_a_politeness_word_does_not_hide_the_request_for_help(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.HELP


@pytest.mark.parametrize("utterance", ["конец игры это мат или пат", "что бывает в конце игры"])
def test_naming_the_end_of_a_game_as_a_term_does_not_end_the_game(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.RESIGN


@pytest.mark.parametrize(
    "utterance",
    [
        "не заканчивай игру отмени ход",
        "не надо заканчивать игру отмени ход",
        "не надо сейчас заканчивать игру отмени ход",
    ],
)
def test_a_negated_end_of_game_leaves_the_command_that_follows_it_alone(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.UNDO


@pytest.mark.parametrize(
    "utterance",
    [
        "не заканчивай игру",
        "не надо заканчивать игру",
        "не хочу завершать игру",
        "давай не будем заканчивать партию",
        "не буду заканчивать партию",
        "не хочу больше завершать партию",
        "завершать игру не надо",
        "завершать шахматную партию не надо",
        "завершать игру мне не надо",
    ],
)
def test_a_wish_between_the_refusal_and_the_end_of_the_game_still_refuses(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.RESIGN


@pytest.mark.parametrize("utterance", ["не хочу играть закончи игру", "не хочу сейчас закончи игру"])
def test_a_refusal_to_play_still_ends_the_game_it_asks_to_end(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.RESIGN


@pytest.mark.parametrize("utterance", ["что значит завершить партию", "что такое закончить игру"])
def test_asking_what_ending_a_game_means_explains_it_instead_of_ending_one(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.HELP


@pytest.mark.parametrize("utterance", ["можно ли закончить игру", "все я сдаюсь что значит партия окончена"])
def test_a_question_about_ending_the_game_that_asks_for_one_still_ends_it(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.RESIGN


@pytest.mark.parametrize("utterance", ["почему ты закончила эту игру", "кто завершил игру"])
def test_asking_about_a_game_that_already_ended_does_not_end_this_one(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.RESIGN


@pytest.mark.parametrize(
    "utterance", ["не заканчивай игру а теперь закончи игру", "что значит мат а теперь закончи игру"]
)
def test_the_clause_an_utterance_ends_on_is_the_one_that_is_answered(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.RESIGN


@pytest.mark.parametrize(
    "utterance",
    ["помоги с ходом", "помоги пожалуйста с ходом", "помоги пожалуйста мне с ходом", "помогите мне с ходом"],
)
def test_asking_for_help_with_one_move_still_reaches_the_trainer(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.TRAINING


@pytest.mark.parametrize("utterance", ["повернуть ход", "вывернуть ход", "вернуть ход"])
def test_asr_variants_of_taking_a_move_back_still_undo(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is CommandKind.UNDO


@pytest.mark.parametrize("utterance", ["поверни доску", "поверни доску чей ход"])
def test_turning_the_board_is_not_taking_a_move_back(utterance: str) -> None:
    assert route(utterance, chess.Board()).kind is not CommandKind.UNDO


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("продолжи", CommandKind.CONTINUE),
        ("продолжите", CommandKind.CONTINUE),
        ("продолжайте", CommandKind.CONTINUE),
        ("продолжите пожалуйста", CommandKind.CONTINUE),
        ("продолжай", CommandKind.CONTINUE),
        ("вперед", CommandKind.BACKCHANNEL),
    ],
)
def test_a_short_go_on_is_answered_instead_of_ignored(utterance: str, expected: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is expected


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [
        ("громкость 3", CommandKind.PLATFORM),
        ("громкость 88", CommandKind.PLATFORM),
        ("алиса громкость на 4", CommandKind.PLATFORM),
        ("алиса сделай звук на шестерку", CommandKind.PLATFORM),
        ("включите погромче", CommandKind.PLATFORM),
        ("не слышу сделайте погромче", CommandKind.PLATFORM),
        ("потише", CommandKind.PLATFORM),
        ("прибавь звук", CommandKind.PLATFORM),
        ("убавь звук", CommandKind.PLATFORM),
        ("погаси свет", CommandKind.PLATFORM),
        ("ну пока", CommandKind.EXIT),
        ("пока алиса", CommandKind.EXIT),
        ("стоп шахматы", CommandKind.EXIT),
        ("стоп пожалуйста", CommandKind.EXIT),
        ("заверши сессию", CommandKind.EXIT),
        ("давайте выйдем из игры", CommandKind.EXIT),
        ("юра пока", CommandKind.EXIT),
        ("пока юра", CommandKind.EXIT),
        ("ладно пока", CommandKind.EXIT),
        ("все пока", CommandKind.EXIT),
        ("пока пожалуйста", CommandKind.EXIT),
        ("выключи шахматную программу", CommandKind.EXIT),
        ("выключи программу для шахмат", CommandKind.EXIT),
        ("как мне выйти отсюда", CommandKind.EXIT_CONFIRM),
        ("выйти из режима шахматы", CommandKind.EXIT_CONFIRM),
        ("может в другую игру поиграем", CommandKind.PLATFORM),
        ("а вы сделайте погромче", CommandKind.PLATFORM),
        ("музыку включи", CommandKind.PLATFORM),
        ("включи лучше музыку", CommandKind.PLATFORM),
        ("включить российские новости", CommandKind.PLATFORM),
        ("выключи телевизор", CommandKind.PLATFORM),
        ("выключи свет", CommandKind.PLATFORM),
        ("алиса хватит какие игры у тебя есть", CommandKind.PLATFORM),
        ("алиса хватит давай во что нибудь другое поиграем", CommandKind.PLATFORM),
        ("алиса хватит давай лучше поиграем в другие шашки и шахматы", CommandKind.PLATFORM),
        ("стоп навык", CommandKind.EXIT),
        ("алиса стоп навык", CommandKind.EXIT),
        ("а можешь постопить", CommandKind.EXIT),
        ("алиса прекрати", CommandKind.EXIT),
        ("алиса нам это не надо стоп", CommandKind.EXIT),
        ("алис хватит стоп", CommandKind.EXIT),
        ("все алиса выключи хватит", CommandKind.EXIT),
        ("пока", CommandKind.EXIT),
        ("алиса пока", CommandKind.EXIT),
        ("все у меня шахмат перед глазами нет пока пока", CommandKind.EXIT),
        ("выключи себя", CommandKind.EXIT),
        ("алисонька отключай меня", CommandKind.EXIT),
        ("алиса выключай шахматы", CommandKind.EXIT),
        ("алиса выключи эту игру", CommandKind.EXIT),
        ("выключите эту игру", CommandKind.EXIT),
        ("алиса выключи эту программу с шахматами", CommandKind.EXIT),
        ("алиса отключи этот режим шахмат", CommandKind.EXIT),
        ("алиса отключить этот навык", CommandKind.EXIT),
        ("алиса как отключить этот навык", CommandKind.EXIT),
        ("а как отключиться", CommandKind.EXIT),
        ("алиса помощь как выключить", CommandKind.EXIT),
        ("закончить сессию", CommandKind.EXIT),
        ("все выходим из игры", CommandKind.EXIT),
        ("выходим выходим из игры", CommandKind.EXIT),
        ("все надоели мне шахматы", CommandKind.EXIT),
        ("алиса стоп игра", CommandKind.RESIGN),
        ("алиса давай закончим этот тур", CommandKind.RESIGN),
    ],
)
def test_leaving_and_asking_alice_are_told_apart(utterance: str, kind: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is kind


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [
        ("выключи режим тренера", CommandKind.TRAINING),
        ("выключить тренера", CommandKind.TRAINING),
        ("как отключить тренера", CommandKind.TRAINING),
        ("выключи подсказки", CommandKind.TRAINING),
        ("выключи звук", CommandKind.PREFERENCE),
        ("как отключить звуки", CommandKind.PREFERENCE),
        ("включи звуки", CommandKind.PREFERENCE),
        ("выйти из задач", CommandKind.PUZZLE),
        ("говори громче", CommandKind.UNKNOWN),
        ("говори потише", CommandKind.UNKNOWN),
        ("говори быстрее", CommandKind.PREFERENCE),
        ("говори медленнее", CommandKind.PREFERENCE),
        ("тише едешь дальше будешь", CommandKind.UNKNOWN),
        ("подожди тише", CommandKind.UNKNOWN),
        ("не хочу играть за черных", CommandKind.NEW_GAME),
        ("как выйти из шаха", CommandKind.UNKNOWN),
        ("как выйти конем на эф три", CommandKind.MOVE),
        ("не выключай шахматы", CommandKind.UNKNOWN),
        ("не отключай меня", CommandKind.UNKNOWN),
        ("не надо выключать шахматы", CommandKind.UNKNOWN),
        ("выключи шахматного тренера", CommandKind.UNKNOWN),
        ("надоела эта шахматная задача", CommandKind.UNKNOWN),
        ("убери шахматную доску с экрана", CommandKind.UNKNOWN),
        ("пока пока пешка е два е четыре", CommandKind.MOVE),
        ("я пока пока думаю конь эф три", CommandKind.MOVE),
        ("хватит давай другую партию", CommandKind.UNKNOWN),
        ("а можешь постопить часы", CommandKind.UNKNOWN),
        ("давай поиграем в другую партию", CommandKind.START),
        ("поиграем в другой дебют", CommandKind.UNKNOWN),
        ("продолжи историю партии", CommandKind.UNKNOWN),
        ("включи светлые фигуры снизу", CommandKind.UNKNOWN),
        ("какая сейчас громкость твоего ответа", CommandKind.UNKNOWN),
        ("не меняй громкость конь эф три", CommandKind.MOVE),
        ("звук на доске пропал конь эф три", CommandKind.MOVE),
        ("давай закончим тур", CommandKind.UNKNOWN),
        ("не выключай шахматы а теперь конь эф три", CommandKind.MOVE),
        ("не отключай меня а сейчас покажи доску", CommandKind.POSITION_QUERY),
        ("не хочу выключать шахматы", CommandKind.UNKNOWN),
        ("не вздумай выключать шахматы", CommandKind.UNKNOWN),
        ("не говори до свидания", CommandKind.UNKNOWN),
        ("не говори пока пока", CommandKind.UNKNOWN),
        ("конь эф три пока пока", CommandKind.MOVE),
        ("не прибавляй звук", CommandKind.UNKNOWN),
        ("не убавляй звук конь эф три", CommandKind.MOVE),
        ("громкость не меняй", CommandKind.UNKNOWN),
        ("громкость ответа меня устраивает", CommandKind.UNKNOWN),
        ("звук на доске", CommandKind.UNKNOWN),
        ("как выйти из этого неприятного шаха", CommandKind.UNKNOWN),
        ("как выйти из партии победителем", CommandKind.UNKNOWN),
        ("я не хочу выйти из навыка", CommandKind.UNKNOWN),
        ("мне не надоели шахматы", CommandKind.UNKNOWN),
        ("не завершай сессию", CommandKind.UNKNOWN),
        ("не нужно прямо сейчас выключать шахматы", CommandKind.UNKNOWN),
        ("давай сыграем в другую игру", CommandKind.START),
        ("стоп разбор", CommandKind.REVIEW),
        ("закончить партию", CommandKind.RESIGN),
        ("завершить игру", CommandKind.RESIGN),
        ("стоп игра", CommandKind.RESIGN),
        ("выходим конем на эф три", CommandKind.MOVE),
        ("не хочу играть белыми давай черными", CommandKind.COLOR_CHOICE),
        ("подожди пока я думаю", CommandKind.UNKNOWN),
        ("пока не знаю", CommandKind.UNKNOWN),
    ],
)
def test_leaving_never_steals_a_command_that_stays_in_the_skill(utterance: str, kind: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is kind


@pytest.mark.parametrize(
    ("utterance", "wish"),
    [
        ("ты кто", PersonaWish.WHO),
        ("кто ты", PersonaWish.WHO),
        ("а ты кто такой", PersonaWish.WHO),
        ("эй мужик ты кто", PersonaWish.WHO),
        ("ты кто такой юра или алиса", PersonaWish.WHO),
        ("ты не алиса ты юрий", PersonaWish.WHO),
        ("а он робот", PersonaWish.WHO),
        ("скажи юре то что он проиграл", PersonaWish.WHO),
        ("кто такой юра", PersonaWish.WHO),
        ("где сейчас юра", PersonaWish.PRESENCE),
        ("а где же юра", PersonaWish.PRESENCE),
        ("юрий тут", PersonaWish.PRESENCE),
        ("где юра", PersonaWish.PRESENCE),
        ("куда делся юра", PersonaWish.PRESENCE),
        ("а юра здесь", PersonaWish.PRESENCE),
        ("без юры", PersonaWish.PRESENCE),
        ("почему юра со мной не разговаривает", PersonaWish.SILENCE),
        ("почему юра опять со мной не разговаривает", PersonaWish.SILENCE),
        ("почему у тебя мужской голос", PersonaWish.VOICE),
        ("почему ты мужским голосом разговариваешь теперь", PersonaWish.VOICE),
        ("поменяй голос на девушку", PersonaWish.VOICE),
        ("алиса измени голос", PersonaWish.VOICE),
        ("алис а почему у тебя был такой голос", PersonaWish.VOICE),
        ("мне нужен мужской не женский голос", PersonaWish.VOICE),
        ("вы девушки или мальчик", PersonaWish.VOICE),
    ],
)
def test_a_question_about_yura_is_told_from_a_question_about_the_board(utterance: str, wish: PersonaWish) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.PERSONA
    assert routed.persona is not None
    assert routed.persona.wish is wish


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [
        ("юра", CommandKind.ATTENTION),
        ("алиса", CommandKind.ATTENTION),
        ("где юра поставил коня", CommandKind.POSITION_QUERY),
        ("где мой конь", CommandKind.POSITION_QUERY),
        ("юра играет черными", CommandKind.UNKNOWN),
        ("хочу чтобы юра играл черными", CommandKind.UNKNOWN),
        ("говори медленнее голосом алисы", CommandKind.PREFERENCE),
        ("можно играть не голосом а визуально", CommandKind.SCREEN),
        ("рокировка большое спасибо", CommandKind.ILLEGAL_MOVE),
        ("пешка е два е четыре спасибо", CommandKind.MOVE),
        ("спасибо алиса все до свидания", CommandKind.EXIT),
        ("хватит ты меня достала", CommandKind.EXIT),
        ("почему мой голос не слышно", CommandKind.UNKNOWN),
        ("почему ты не распознаешь мой голос", CommandKind.UNKNOWN),
        ("а четыре", CommandKind.MOVE),
        ("а конь эф три", CommandKind.MOVE),
        ("ну и что дальше", CommandKind.GAME_FACT),
        ("все хорошо", CommandKind.UNKNOWN),
        # A filler is only an address when a name follows it; alone it stays part of the phrase.
        ("а стоп", CommandKind.UNKNOWN),
        ("ну хватит", CommandKind.UNKNOWN),
        ("ладно все", CommandKind.UNKNOWN),
        ("ладно сдаюсь", CommandKind.RESIGN),
        ("огромное спасибо", CommandKind.SOCIAL),
        ("спасибо вам", CommandKind.SOCIAL),
        ("спасибо пока", CommandKind.EXIT),
        ("спасибо новая игра", CommandKind.NEW_GAME),
        ("спасибо сдаюсь", CommandKind.RESIGN),
    ],
)
def test_a_name_in_the_middle_of_a_command_is_not_a_question_about_yura(utterance: str, kind: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is kind


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [
        ("алиса привет", CommandKind.SOCIAL),
        ("алис привет", CommandKind.SOCIAL),
        ("юра пока", CommandKind.EXIT),
        ("твой ход юра", CommandKind.CONTINUE),
        ("юра давай белыми", CommandKind.COLOR_CHOICE),
        ("юра пешка дэ два дэ четыре", CommandKind.MOVE),
        ("алиса разбор", CommandKind.REVIEW),
        ("алиса разборчиво", CommandKind.UNKNOWN),
        ("алиса продолжай трек", CommandKind.UNKNOWN),
        ("скажи юре где стоит король", CommandKind.POSITION_QUERY),
        ("спроси у юры чей ход", CommandKind.POSITION_QUERY),
        ("пусть юра повторит ход", CommandKind.POSITION_QUERY),
        ("пусть юра назовет последний ход", CommandKind.POSITION_QUERY),
        ("алиса пусть юра повторит ход", CommandKind.POSITION_QUERY),
        ("ну юра твой ход", CommandKind.CONTINUE),
        ("юр твой ход", CommandKind.CONTINUE),
    ],
)
def test_a_command_said_to_yura_by_name_is_still_the_command(utterance: str, kind: CommandKind) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is kind
    assert routed.normalized.text == utterance


@pytest.mark.parametrize(
    ("utterance", "theme"),
    [
        ("задачки", None),
        ("давай задачу", None),
        ("можно задачу", None),
        ("можно мне задачу", None),
        ("решим задачу", None),
        ("открой задачи", None),
        ("перейдем к задачам", None),
        ("режим задачи", None),
        ("раздел задачи", None),
        ("тактическую задачу", None),
        ("шахматную задачу", None),
        ("шахматная головоломка", None),
        ("тактика", None),
        ("головоломка", None),
        ("этюд", None),
        ("дай этюд", None),
        ("хочу этюд", None),
        ("шахматный этюд", None),
        ("мат в 1", "mateIn1"),
        ("мат в 1 ход", "mateIn1"),
        ("мат в 2 хода", "mateIn2"),
        ("дай мат в один ход", "mateIn1"),
        ("давай мат в два хода", "mateIn2"),
        ("задачу с вилкой", "fork"),
        ("задачу про связку", "pin"),
        ("задача сквозной удар", "skewer"),
    ],
)
def test_a_puzzle_is_asked_for_in_more_than_one_way(utterance: str, theme: str | None) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is CommandKind.PUZZLE
    assert routed.puzzle is not None
    assert routed.puzzle.question is PuzzleQuestion.START
    assert routed.puzzle.theme == theme


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [
        ("мат", CommandKind.UNKNOWN),
        ("какая тактика лучше", CommandKind.UNKNOWN),
        ("тебе мат в 3 хода", CommandKind.CLARIFY),
        ("мат в три хода", CommandKind.CLARIFY),
        ("тактика ферзя", CommandKind.CLARIFY),
        ("справка по задачам", CommandKind.HELP),
        ("темы задач", CommandKind.HELP),
        ("тема задач", CommandKind.HELP),
        ("какие у тебя есть задачи", CommandKind.HELP),
        ("не давай задачу", CommandKind.UNKNOWN),
        ("не дай задачу", CommandKind.UNKNOWN),
        ("не хочу задачу", CommandKind.UNKNOWN),
        ("не надо задачу", CommandKind.UNKNOWN),
        ("не буду решать задачи", CommandKind.UNKNOWN),
        ("не показывай решение", CommandKind.UNKNOWN),
        ("не могу решить какой ход лучше", CommandKind.TRAINING),
        ("не могу решить стоит ли мне ходить конем", CommandKind.TRAINING),
    ],
)
def test_asking_about_puzzles_is_not_asking_for_one(utterance: str, kind: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is kind


@pytest.mark.parametrize(
    ("utterance", "question"),
    [
        ("выйти из задач", PuzzleQuestion.EXIT),
        ("следующая задача", PuzzleQuestion.NEXT),
        ("повтори задачу", PuzzleQuestion.REPEAT),
        ("повторить задачу", PuzzleQuestion.REPEAT),
        ("можно повторить задачу", PuzzleQuestion.REPEAT),
        ("напомнить задачу", PuzzleQuestion.REPEAT),
        ("покажи решение", PuzzleQuestion.SOLUTION),
        ("не знаю решение", PuzzleQuestion.SOLUTION),
        ("не могу решить задачу", PuzzleQuestion.SOLUTION),
        ("я не могу решить эту задачу", PuzzleQuestion.SOLUTION),
        ("не знаю покажи решение", PuzzleQuestion.SOLUTION),
        ("не хочу подсказку покажи решение", PuzzleQuestion.SOLUTION),
        ("не давай новую задачу повтори условие", PuzzleQuestion.REPEAT),
        ("не могу решить эту задачу повтори условие", PuzzleQuestion.REPEAT),
        ("не надо решение следующую задачу", PuzzleQuestion.NEXT),
        ("какая у меня серия", PuzzleQuestion.STREAK),
        ("какие задачи я решал", PuzzleQuestion.HISTORY),
        ("решенные задачи", PuzzleQuestion.HISTORY),
        ("открой мои решенные задачи", PuzzleQuestion.HISTORY),
    ],
)
def test_the_puzzle_commands_keep_their_own_question(utterance: str, question: PuzzleQuestion) -> None:
    routed = route(utterance, chess.Board())

    assert routed.puzzle is not None
    assert routed.puzzle.question is question


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [
        ("разобрать партию", CommandKind.REVIEW),
        ("начать новую игру", CommandKind.NEW_GAME),
        ("решить задачу", CommandKind.PUZZLE),
    ],
)
def test_every_phrase_the_game_over_prompt_names_is_a_command(utterance: str, kind: CommandKind) -> None:
    assert utterance in NEXT_STEP_PROMPT
    assert route(utterance, chess.Board()).kind is kind
