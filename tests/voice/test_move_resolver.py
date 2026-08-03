"""Voice recognition of moves, from normalisation to routing.

Phrasings are morphological variants and the standard Russian pronunciation of
the files; captured Alice transcripts fold in as extra parametrised rows.
"""

from __future__ import annotations

import chess
import pytest

from yura_chess.application.command_router import (
    CommandKind,
    PendingClarification,
    PreferenceChange,
    RematchColor,
    RematchRequest,
    TrainingQuestion,
    confirmation_answer,
    route,
)
from yura_chess.domain.preferences import BoardOrientation, DetailLevel, NotationStyle, PauseStyle
from yura_chess.voice.move_resolver import resolve
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
