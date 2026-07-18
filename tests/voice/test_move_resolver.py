"""Voice recognition of moves, from normalisation to routing.

Phrasings are morphological variants and the standard Russian pronunciation of
the files; captured Alice transcripts fold in as extra parametrised rows.
"""

from __future__ import annotations

import chess
import pytest

from yura_chess.application.command_router import CommandKind, PendingClarification, route
from yura_chess.voice.move_resolver import resolve
from yura_chess.voice.normalizer import normalize
from yura_chess.voice.types import ResolutionStatus

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
        ("какая позиция", CommandKind.POSITION_QUERY),
        ("где стоит мой король", CommandKind.POSITION_QUERY),
        ("что ты услышала", CommandKind.REPEAT_HEARD),
    ],
)
def test_control_commands_are_separated_before_move_resolution(utterance: str, expected: CommandKind) -> None:
    routed = route(utterance, chess.Board())

    assert routed.kind is expected
    assert routed.move is None


def test_repeat_heard_answers_with_the_previous_utterance() -> None:
    routed = route("что ты услышала", chess.Board(), last_heard="пешка е два е четыре")

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
