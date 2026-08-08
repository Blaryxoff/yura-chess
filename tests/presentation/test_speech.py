"""Everything the skill says must be complete without a screen.

The pronunciation is checked against the normaliser: whatever the skill spells
out has to parse back into the same square, otherwise the player cannot repeat
what they just heard.
"""

from __future__ import annotations

import chess
import pytest

from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.domain.preferences import NotationStyle, PauseStyle
from yura_chess.domain.results import GameEnd, GameOutcome, TurnResult, TurnStatus
from yura_chess.presentation.move_speech import (
    ENGINE_MOVE_PREFIX,
    PAUSE_MARKUP,
    PLAYER_MOVE_PREFIX,
    SoundEvent,
    SoundLibrary,
    Speech,
    add_move_sounds,
    add_pauses,
    add_sound,
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
from yura_chess.presentation.response_composer import NEXT_STEP_PROMPT, compose_turn
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


def test_one_sound_is_added_to_tts_without_changing_visible_or_spoken_words() -> None:
    library = SoundLibrary("start.opus", "move.opus", "check.opus", "mate.opus", "dialogs-upload/skill/audio.opus")
    speech = add_sound(Speech.of("Задача решена."), SoundEvent.SUCCESS, True, library)

    assert speech.text == "Задача решена."
    assert speech.tts == '<speaker audio="dialogs-upload/skill/audio.opus"> Задача решена.'


def test_each_half_of_a_turn_is_sounded_on_the_words_that_name_its_move() -> None:
    library = SoundLibrary(
        "alice-sounds-start.opus",
        "alice-sounds-move.opus",
        "alice-sounds-check.opus",
        "alice-sounds-mate.opus",
        "alice-sounds-win.opus",
    )
    both = Speech.of(f"{PLAYER_MOVE_PREFIX}e2 e4. {ENGINE_MOVE_PREFIX}пешка e7 e5.")

    sounded = add_move_sounds(both, None, SoundEvent.MOVE, SoundEvent.CHECK, True, library)

    assert sounded.text == both.text
    assert sounded.tts == (
        '<speaker audio="alice-sounds-move.opus"> Ваш ход: е два е четыре. '
        '<speaker audio="alice-sounds-check.opus"> Мой ход. пешка е семь е пять.'
    )


def test_a_move_the_answer_does_not_name_is_never_sounded() -> None:
    """A settled or raced engine reply carries a move whose words it never says."""
    library = SoundLibrary(
        "alice-sounds-start.opus",
        "alice-sounds-move.opus",
        "alice-sounds-check.opus",
        "alice-sounds-mate.opus",
        "alice-sounds-win.opus",
    )
    silent = Speech.of("Ваш ход.")

    assert add_move_sounds(silent, None, SoundEvent.CHECK, None, True, library) == silent
    # An answer-level cue belongs to no ply and is not withheld with them.
    assert add_move_sounds(silent, SoundEvent.START, SoundEvent.CHECK, None, True, library).tts == (
        '<speaker audio="alice-sounds-start.opus"> Ваш ход.'
    )


def test_disabled_or_untrusted_sound_markup_is_not_added() -> None:
    library = SoundLibrary('bad"> injected', "move", "check", "mate", "success")
    speech = Speech.of("Новая партия.")

    assert add_sound(speech, SoundEvent.START, False, library) == speech
    assert add_sound(speech, SoundEvent.START, True, library) == speech


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


@pytest.mark.parametrize(
    "utterance",
    ["как ты пошла", "как ты пошёл", "как ты походила", "напомни свой ход", "напомни, как ты пошла", "напомни ход"],
)
def test_asking_how_the_opponent_moved_reads_the_move_and_not_the_board(utterance: str) -> None:
    board = chess.Board()
    board.push_uci("e2e4")

    answer = answer_position_query(utterance, board)

    assert answer.query is PositionQuery.LAST_MOVE
    assert answer.speech.text == "Последний ход: пешка e2 e4."


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


def test_moves_can_be_selected_by_their_number_from_the_start() -> None:
    board = chess.Board()
    for move in ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"):
        board.push_uci(move)

    second_black = answer_position_query("какой был второй ход черных", board)
    first_overall = answer_position_query("какой был первый ход", board)
    third_white = answer_position_query("назови третий ход белых", board)
    digits = answer_position_query("какой был 2 ход черных", board)

    assert second_black.query is PositionQuery.NUMBERED_MOVE
    assert second_black.speech.text == "Второй ход черных: конь b8 c6."
    assert first_overall.speech.text == "Первый ход: пешка e2 e4."
    assert third_white.speech.text == "Третий ход белых: слон f1 b5."
    assert digits.speech.text == second_black.speech.text


def test_a_move_number_the_game_never_reached_is_answered_honestly() -> None:
    board = chess.Board()
    board.push_uci("e2e4")

    answer = answer_position_query("какой был десятый ход", board)

    assert answer.query is PositionQuery.NUMBERED_MOVE
    assert answer.speech.text == "Не могу найти такой ход: в партии был только один ход."


def test_counting_back_still_wins_over_the_move_number() -> None:
    board = chess.Board()
    for move in ("e2e4", "e7e5", "g1f3"):
        board.push_uci(move)

    answer = answer_position_query("что было два хода назад", board)

    assert answer.query is PositionQuery.HISTORY
    assert "пешка e7 e5" in answer.speech.text


def test_a_single_rank_can_be_read_on_its_own() -> None:
    board = chess.Board()
    board.push_uci("e2e4")

    who = answer_position_query("кто стоит на седьмой горизонтали", board)
    what = answer_position_query("какая позиция на четвертой горизонтали", board)
    empty = answer_position_query("что находится на пятой горизонтали", board)

    assert who.query is PositionQuery.RANK
    assert who.speech.text.startswith("Седьмая горизонталь: черные — пешка a7")
    assert what.speech.text == "Четвертая горизонталь: белые — пешка e4."
    assert empty.speech.text == "Пятая горизонталь пуста."


def test_history_query_reports_when_the_game_is_too_short() -> None:
    board = chess.Board()
    board.push_uci("e2e4")

    answer = answer_position_query("что сделали черные два хода назад", board)

    assert answer.query is PositionQuery.HISTORY
    assert answer.speech.text == "Ходов у черных еще не было."


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

    assert speech.text == f"Мат. Черные выиграли. Вы проиграли. {NEXT_STEP_PROMPT}"


def test_a_finished_game_ends_by_naming_what_to_do_next() -> None:
    speech = compose_turn(
        _result(
            TurnStatus.GAME_OVER,
            outcome=GameOutcome(GameEnd.RESIGNATION, PlayerColor.BLACK),
            game_status=GameStatus.RESIGNED,
        )
    )

    assert speech.text == f"Вы сдались. Партия окончена. {NEXT_STEP_PROMPT}"


def test_a_game_still_being_played_is_not_told_what_to_do_next() -> None:
    speech = compose_turn(_result(TurnStatus.OK))

    assert NEXT_STEP_PROMPT not in speech.text


def test_what_to_do_next_is_named_after_every_sound_of_the_answer() -> None:
    library = SoundLibrary(
        "alice-sounds-start.opus",
        "alice-sounds-move.opus",
        "alice-sounds-check.opus",
        "alice-sounds-mate.opus",
        "alice-sounds-win.opus",
    )
    board = chess.Board(MATE_IN_ONE_FEN)
    move = chess.Move.from_uci("a1a8")
    after = board.copy(stack=False)
    after.push(move)
    speech = compose_turn(
        _result(
            TurnStatus.GAME_OVER,
            engine_move=move.uci(),
            fen=after.fen(),
            player_color=PlayerColor.BLACK,
            game_status=GameStatus.FINISHED,
            outcome=GameOutcome(GameEnd.CHECKMATE, PlayerColor.WHITE),
        ),
        board,
    )

    sounded = add_move_sounds(speech, None, SoundEvent.MOVE, SoundEvent.CHECKMATE, True, library)

    assert sounded.spoken().count("<speaker") == 1
    assert sounded.spoken().endswith(NEXT_STEP_PROMPT)


def test_engine_check_is_not_announced_twice_by_commentary() -> None:
    board = chess.Board("k6r/8/8/8/8/8/8/4K3 b - - 0 1")
    move = chess.Move.from_uci("h8e8")
    after = board.copy(stack=False)
    after.push(move)

    speech = compose_turn(
        _result(TurnStatus.OK, engine_move=move.uci(), fen=after.fen(), player_color=PlayerColor.WHITE),
        board,
        commentary="Вам шах.",
    )

    assert speech.text.lower().count("шах") == 1


def test_engine_checkmate_is_not_announced_twice() -> None:
    board = chess.Board(MATE_IN_ONE_FEN)
    move = chess.Move.from_uci("a1a8")
    after = board.copy(stack=False)
    after.push(move)

    speech = compose_turn(
        _result(
            TurnStatus.GAME_OVER,
            engine_move=move.uci(),
            fen=after.fen(),
            player_color=PlayerColor.BLACK,
            game_status=GameStatus.FINISHED,
            outcome=GameOutcome(GameEnd.CHECKMATE, PlayerColor.WHITE),
        ),
        board,
    )

    assert speech.text.count("Мат.") == 1


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

    assert speech.text == f"{expected} {NEXT_STEP_PROMPT}"


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


def test_short_notation_names_only_where_the_piece_lands() -> None:
    quiet = describe_move(chess.Board(), chess.Move.from_uci("e2e4"), NotationStyle.SHORT)
    capture = describe_move(chess.Board(CAPTURE_FEN), chess.Move.from_uci("a1d1"), NotationStyle.SHORT)
    played = describe_played_move(_after("g1f3"), chess.Move.from_uci("g1f3"), NotationStyle.SHORT)

    assert quiet.text == "пешка e4."
    assert capture.text == "ладья берет ферзя на d1."
    assert played.text == "конь f3."


def test_short_notation_keeps_the_chess_meaning_of_special_moves() -> None:
    castling = describe_move(chess.Board(CASTLING_FEN), chess.Move.from_uci("e1c1"), NotationStyle.SHORT)
    promotion = describe_move(chess.Board(PROMOTION_FEN), chess.Move.from_uci("a7a8q"), NotationStyle.SHORT)
    mate = describe_move(chess.Board(MATE_IN_ONE_FEN), chess.Move.from_uci("a1a8"), NotationStyle.SHORT)

    assert castling.text == "Длинная рокировка."
    assert "и превращается в ферзя" in promotion.text
    assert mate.text.endswith("Мат.")


def test_extended_pauses_only_space_out_the_pronunciation() -> None:
    speech = Speech.of("Мой ход. пешка e2 e4. Шах.")

    paused = add_pauses(speech, PauseStyle.EXTENDED)

    assert paused.text == speech.text
    assert paused.spoken().count(PAUSE_MARKUP) == 2
    assert paused.spoken().replace(PAUSE_MARKUP, "") == speech.spoken()


def test_normal_pauses_add_nothing_and_remove_nothing() -> None:
    speech = Speech.of("Мой ход. пешка e2 e4.")

    assert add_pauses(speech, PauseStyle.NORMAL) == speech


def _after(uci: str) -> chess.Board:
    board = chess.Board()
    board.push_uci(uci)
    return board


@pytest.mark.parametrize(
    "utterance",
    [
        "кто стоит на седьмой горизонтали",
        "прочитай седьмую горизонталь",
        "что на горизонтали семь",
        "покажи горизонталь 7",
        "прочитай горизонталь номер семь",
    ],
)
def test_one_rank_is_read_however_its_number_is_named(utterance: str) -> None:
    answer = answer_position_query(utterance, chess.Board())

    assert answer.query is PositionQuery.RANK
    assert answer.speech.text.startswith("Седьмая горизонталь:")


@pytest.mark.parametrize(
    "utterance",
    ["какой был второй ход", "какой был ход номер два"],
)
def test_a_move_number_is_understood_however_it_is_worded(utterance: str) -> None:
    board = chess.Board()
    for uci in ("e2e4", "e7e5"):
        board.push_uci(uci)

    answer = answer_position_query(utterance, board)

    assert answer.query is PositionQuery.NUMBERED_MOVE
    assert answer.speech.text == "Второй ход: пешка e7 e5."


def test_a_move_number_past_ten_is_named_in_words() -> None:
    board = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1", "f8e7", "f1e1"):
        board.push_uci(uci)

    answer = answer_position_query("какой был одиннадцатый ход", board)

    assert answer.query is PositionQuery.NUMBERED_MOVE
    assert answer.speech.text == "Одиннадцатый ход: ладья f1 e1."


@pytest.mark.parametrize("utterance", ["назови второй полный ход", "какой был полный ход номер два"])
def test_a_full_move_number_counts_both_halves(utterance: str) -> None:
    board = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
        board.push_uci(uci)

    answer = answer_position_query(utterance, board)

    assert answer.query is PositionQuery.NUMBERED_MOVE
    assert answer.speech.text == "Второй полный ход. Белые — конь g1 f3. Черные — конь b8 c6."


def test_a_full_move_still_unanswered_names_only_the_half_played() -> None:
    board = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3"):
        board.push_uci(uci)

    assert answer_position_query("второй полный ход", board).speech.text == "Второй ход белых: конь g1 f3."


def test_a_full_move_beyond_the_game_is_counted_in_full_moves() -> None:
    board = chess.Board()
    for uci in ("e2e4", "e7e5"):
        board.push_uci(uci)

    answer = answer_position_query("какой был второй полный ход", board)

    assert answer.speech.text == "Не могу найти такой ход: в партии был только один полный ход."


def test_a_full_move_of_one_side_is_read_as_that_sides_move() -> None:
    board = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
        board.push_uci(uci)

    assert answer_position_query("второй полный ход черных", board).speech.text == "Второй ход черных: конь b8 c6."
