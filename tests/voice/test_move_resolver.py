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
        ("какой уровень сложности", CommandKind.LEVEL_QUERY),
        ("какая позиция", CommandKind.POSITION_QUERY),
        ("где стоит мой король", CommandKind.POSITION_QUERY),
        ("где белые слоны", CommandKind.POSITION_QUERY),
        ("какой был последний ход", CommandKind.POSITION_QUERY),
        ("какой был последний ход черных", CommandKind.POSITION_QUERY),
        ("что сделали черные четыре хода назад", CommandKind.POSITION_QUERY),
        ("что было четыре хода назад", CommandKind.POSITION_QUERY),
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
