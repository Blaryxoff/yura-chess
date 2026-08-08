"""End-to-end voice conversation tests without the Alice transport."""

from __future__ import annotations

import chess
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import (
    CommandKind,
    PendingClarification,
    ReviewQuestion,
    TrainingQuestion,
    route,
)
from yura_chess.application.conversation import (
    MAX_SKILL_LEVEL,
    ConversationReply,
    ConversationService,
    ConversationState,
    _board_after_player,
    _engine_sound,
    _player_move_echo,
    _player_sound,
)
from yura_chess.application.game_service import RequestContext
from yura_chess.application.puzzle_service import PuzzleService
from yura_chess.domain.analysis import MoveCandidate, PositionAnalysis, Score
from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.domain.preferences import BoardOrientation, DetailLevel, NotationStyle, PauseStyle
from yura_chess.domain.results import GameEnd, GameOutcome, TurnResult, TurnStatus
from yura_chess.presentation.board_image import position_hash
from yura_chess.presentation.help_speech import SECTIONS, HelpState, HelpTopic
from yura_chess.presentation.move_speech import PAUSE_MARKUP, SoundEvent, Speech
from yura_chess.presentation.response_composer import BoardCard
from yura_chess.settings import Settings
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import AsrTranscriptRow, UsageRequestRow
from yura_chess.storage.preferences_repository import PreferencesRepository
from yura_chess.storage.review_repository import ReviewRepository
from yura_chess.storage.usage_repository import request_key

pytestmark = pytest.mark.anyio

OWNER = "c" * 64
CASTLING_FEN = "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1"
MATE_IN_ONE_FEN = "6k1/5ppp/8/8/8/8/8/R5RK w - - 0 1"


class FakeEngine:
    def __init__(self) -> None:
        self.skill_levels: list[int | None] = []

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        self.skill_levels.append(skill_level)
        return next(iter(board.legal_moves)).uci()

    async def analyse(
        self,
        board: chess.Board,
        search_time: float | None = None,
        candidates: int | None = None,
    ) -> PositionAnalysis:
        moves = [move.uci() for move in board.legal_moves][: candidates or 3]
        return PositionAnalysis(
            fen=board.fen(),
            side_to_move=PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK,
            depth=8,
            candidates=tuple(
                MoveCandidate(move=move, score=Score(centipawns=0), principal_variation=(move,)) for move in moves
            ),
        )


def context(message_id: int, *, new: bool = False, timezone: str | None = None) -> RequestContext:
    value = str(message_id)
    return RequestContext("shell", "conversation", value, value.ljust(64, "0"), new, timezone)


def subject(session_factory: sessionmaker[Session], settings: Settings) -> ConversationService:
    return ConversationService(session_factory, FakeEngine(), settings)


async def test_voice_move_runs_through_router_game_and_speech(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    reply = await conversation.handle(
        OWNER,
        "пешка е два е четыре",
        context(2),
        started.state,
    )

    assert reply.turn is not None
    assert reply.turn.player_move == "e2e4"
    assert reply.turn.engine_move is not None
    assert "Ваш ход: пешка e2 e4" in reply.speech.text
    assert "Мой ход" in reply.speech.text
    assert reply.speech.tts is not None and "alice-sounds-game-ping-1.opus" in reply.speech.tts


async def test_start_sound_and_durable_voice_switch(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    disabled = await conversation.handle(OWNER, "выключи звуки", context(2), started.state)
    moved = await conversation.handle(OWNER, "пешка е два е четыре", context(3), disabled.state)

    assert started.speech.tts is not None and "alice-sounds-game-boot-1.opus" in started.speech.tts
    assert disabled.speech.text == "Звуки выключены."
    assert moved.speech.tts is not None
    assert "<speaker" not in moved.speech.tts
    with session_scope(session_factory) as session:
        assert PreferencesRepository(session).load(OWNER).sounds_enabled is False


def test_the_player_move_is_echoed_by_name_the_way_the_engine_move_is() -> None:
    base = {
        "game_id": "echo-game",
        "revision": 2,
        "player_color": PlayerColor.WHITE,
        "game_status": GameStatus.ACTIVE,
        "status": TurnStatus.OK,
    }
    quiet = TurnResult(fen=chess.STARTING_FEN, moves=("e2e4",), player_move="e2e4", **base)
    after_pawn = chess.Board()
    after_pawn.push_uci("e2e4")
    castled = TurnResult(fen=CASTLING_FEN, moves=("e1g1",), player_move="e1g1", **base)
    after_castling = chess.Board(CASTLING_FEN)
    after_castling.push_uci("e1g1")
    mating = TurnResult(
        fen="6k1/5ppp/8/8/8/8/8/6RK b - - 0 1",
        moves=("a1a8",),
        player_move="a1a8",
        outcome=GameOutcome(GameEnd.CHECKMATE, PlayerColor.WHITE),
        **{key: value for key, value in base.items() if key != "game_status"},
        game_status=GameStatus.FINISHED,
    )
    after_mate = chess.Board(MATE_IN_ONE_FEN)
    after_mate.push_uci("a1a8")
    owed = TurnResult(fen=chess.STARTING_FEN, moves=("e2e4",), player_move="e2e4", settles_owed_reply=True, **base)

    assert _player_move_echo(quiet, after_pawn, NotationStyle.FULL) == "пешка e2 e4."
    assert _player_move_echo(quiet, after_pawn, NotationStyle.SHORT) == "пешка e4."
    assert _player_move_echo(castled, after_castling, NotationStyle.FULL) == "Короткая рокировка."
    # The outcome sentence already says «Мат»; the echo must not say it twice.
    assert _player_move_echo(mating, after_mate, NotationStyle.FULL) == "ладья a1 a8."
    # Without the position the move was made in, only the coordinates are left.
    assert _player_move_echo(quiet, None, NotationStyle.FULL) == "e2 e4."
    assert _player_move_echo(owed, after_pawn, NotationStyle.FULL) is None


def test_each_half_of_a_turn_gets_the_cue_of_its_own_move() -> None:
    base = {
        "game_id": "sound-game",
        "revision": 2,
        "moves": ("f2f3",),
        "player_color": PlayerColor.WHITE,
        "player_move": "f2f3",
        "game_status": GameStatus.ACTIVE,
        "status": TurnStatus.OK,
    }
    quiet = chess.Board()
    checking = chess.Board("4k3/8/8/8/8/8/4R3/4K3 b - - 0 1")
    checked = TurnResult(fen="4k3/8/8/8/8/8/4r3/4K3 w - - 0 1", engine_move="e7e2", **base)
    owed = TurnResult(
        fen="4k3/8/8/8/8/8/4r3/4K3 w - - 0 1",
        **{key: value for key, value in base.items() if key != "status"},
        status=TurnStatus.ENGINE_UNAVAILABLE,
    )
    won = TurnResult(
        fen="7k/6Q1/6K1/8/8/8/8/8 b - - 0 1",
        outcome=GameOutcome(GameEnd.CHECKMATE, PlayerColor.WHITE),
        game_status=GameStatus.FINISHED,
        **{key: value for key, value in base.items() if key != "game_status"},
    )
    lost = TurnResult(
        fen="7K/6q1/6k1/8/8/8/8/8 w - - 0 1",
        engine_move="g7g8",
        outcome=GameOutcome(GameEnd.CHECKMATE, PlayerColor.BLACK),
        game_status=GameStatus.FINISHED,
        **{key: value for key, value in base.items() if key != "game_status"},
    )

    assert _player_sound(checked, quiet) is SoundEvent.MOVE
    assert _engine_sound(checked) is SoundEvent.CHECK
    # A check the player gives and the engine parries is still heard on its half.
    assert _player_sound(checked, checking) is SoundEvent.CHECK
    assert _player_sound(won, chess.Board(won.fen)) is SoundEvent.SUCCESS
    assert _engine_sound(won) is None
    assert _player_sound(lost, quiet) is SoundEvent.MOVE
    assert _engine_sound(lost) is SoundEvent.CHECKMATE
    # A check answered by mate keeps both halves: the player's check, then the mate.
    assert _player_sound(lost, checking) is SoundEvent.CHECK
    # The answer keeps the move and asks for «продолжаем»; the engine never replied.
    assert _player_sound(owed, chess.Board(owed.fen)) is SoundEvent.CHECK
    assert _engine_sound(owed) is None


def test_a_turn_settled_elsewhere_reads_the_players_own_position() -> None:
    """1. f3 e5 2. a3 Qh4+ settled elsewhere: «a3» is quiet, the check is Black's."""
    played = ("f2f3", "e7e5", "a2a3", "d8h4")
    settled = chess.Board()
    for uci in played:
        settled.push(chess.Move.from_uci(uci))
    # The engine's reply is already in the history, and it gives check.
    raced = TurnResult(
        game_id="raced-game",
        revision=5,
        fen=settled.fen(),
        moves=played,
        player_color=PlayerColor.WHITE,
        player_move="a2a3",
        game_status=GameStatus.ACTIVE,
        status=TurnStatus.OK,
    )

    quiet = _board_after_player(raced, settled)

    assert settled.is_check()
    assert quiet is not None and not quiet.is_check()
    assert _player_sound(raced, quiet) is SoundEvent.MOVE
    # A history that never contained the move leaves the cue to the safe default.
    assert _board_after_player(raced, chess.Board()) is None


async def test_incomplete_and_compound_moves_get_specific_non_mutating_clarifications(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    incomplete = await conversation.handle(OWNER, "я конем хожу", context(2), started.state)
    compound = await conversation.handle(OWNER, "е 2 е 4 е 7 е 5", context(3), incomplete.state)
    sequenced = await conversation.handle(OWNER, "рокировка потом конь эф три", context(4), compound.state)
    # ASR swallowed the destination file: the queen is not on d2, and the square
    # the player did name must not be read back as where the move ends.
    glued = await conversation.handle(OWNER, "ферзь д 23", context(5), sequenced.state)

    assert incomplete.speech.text == "Куда пойти конем? Назовите поле."
    assert glued.speech.text == "Куда пойти ферзем? Назовите поле."
    assert compound.speech.text == "Я услышал несколько ходов. Назовите только ваш текущий ход."
    assert sequenced.speech.text == "Я услышал несколько ходов. Назовите только ваш текущий ход."
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(started.state.game_id or "", OWNER).moves == ()


async def test_physical_board_setup_and_exit_phrases_are_voice_complete(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    setup = await conversation.handle(OWNER, "ладно сейчас я расставлю", context(1))
    exited = await conversation.handle(OWNER, "убери навык", context(2), setup.state)

    assert "Вы играете белыми, я черными" in setup.speech.text
    assert "назовите только свой ход" in setup.speech.text
    assert exited.end_session is True
    assert "Партия сохранена" in exited.speech.text


async def test_a_loosely_named_exit_is_confirmed_before_the_skill_closes(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    asked = await conversation.handle(OWNER, "выход пожалуйста", context(2), started.state)
    left = await conversation.handle(OWNER, "да", context(3), asked.state)

    assert asked.speech.text == "Выйти из навыка? Скажите «да» или «нет»."
    assert asked.end_session is False
    assert left.end_session is True
    assert "Партия сохранена" in left.speech.text


async def test_a_declined_exit_keeps_the_skill_and_the_game_open(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    asked = await conversation.handle(OWNER, "я хочу выйти", context(2), started.state)
    stayed = await conversation.handle(OWNER, "нет", context(3), asked.state)
    moved = await conversation.handle(OWNER, "е 2 е 4", context(4), stayed.state)

    assert stayed.end_session is False
    assert stayed.state.game_id == started.state.game_id
    assert moved.turn is not None and moved.turn.player_move == "e2e4"


async def test_a_bare_stop_word_still_leaves_without_a_question(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """Alice requires «выход» and «стоп» to close the skill on the spot."""
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    exited = await conversation.handle(OWNER, "выход", context(2), started.state)

    assert exited.end_session is True
    assert exited.speech.text.startswith("До свидания.")


async def test_your_turn_and_repeat_your_move_answer_from_canonical_state(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        game = repository.create_game(OWNER, PlayerColor.WHITE)
        game = repository.append_moves(game.id, OWNER, game.revision, ("e2e4", "e7e5"))
    state = ConversationState(game.id, game.revision)
    conversation = subject(session_factory, offline_settings)

    repeated = await conversation.handle(OWNER, "повтори свой ход", context(1), state)
    prompted = await conversation.handle(OWNER, "алиса твой ход", context(2), repeated.state)

    assert "e7" in repeated.speech.text and "e5" in repeated.speech.text
    assert "Ваш ход" in prompted.speech.text
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(game.id, OWNER).moves == ("e2e4", "e7e5")


async def test_multi_undo_is_spoken_and_rewinds_complete_turns(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        game = repository.create_game(OWNER, PlayerColor.WHITE)
        game = repository.append_moves(
            game.id,
            OWNER,
            game.revision,
            ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"),
        )
    state = ConversationState(game.id, game.revision)

    reply = await subject(session_factory, offline_settings).handle(
        OWNER,
        "откати два полных хода",
        context(1),
        state,
    )

    assert reply.speech.text == "2 полных хода отменено. Ваш ход."
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(game.id, OWNER).moves == ("e2e4", "e7e5")


async def test_confirmation_analytics_are_request_linked_and_not_reported_as_unmatched(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    asked = await conversation.handle(OWNER, "новая игра", context(2), started.state)
    await conversation.handle(OWNER, "да подтверждаю", context(3), asked.state)
    key = request_key("shell", "conversation", "3")

    with session_scope(session_factory) as session:
        usage = session.get(UsageRequestRow, key)
        transcript = session.scalars(select(AsrTranscriptRow).where(AsrTranscriptRow.request_key == key)).one()

    assert usage is not None
    assert (usage.command_kind, usage.resolution_status, usage.routing_outcome) == (
        "confirmation",
        None,
        "confirmation",
    )
    assert transcript.outcome == "confirmation"


async def test_illegal_move_explains_the_rule_without_changing_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    reply = await conversation.handle(OWNER, "пешка е два е пять", context(2), started.state)

    assert reply.turn is None
    assert "Пешка" in reply.speech.text
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert state.moves == ()


async def test_a_move_during_a_pending_engine_turn_resumes_before_explaining_legality(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    game_id = started.state.game_id or ""
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        game = repository.load(game_id, OWNER)
        pending = repository.begin_engine_turn(game.id, OWNER, game.revision, "e2e4", "pending")

    reply = await conversation.handle(
        OWNER,
        "конь эф три",
        context(2),
        ConversationState(game_id=game_id, revision=pending.revision),
    )

    assert "Теперь повторите новый ход" in reply.speech.text
    assert "нельзя" not in reply.speech.text.lower()


async def test_position_and_repeat_heard_are_available_without_alice(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    position = await conversation.handle(OWNER, "что на е два", context(2), started.state)
    repeated = await conversation.handle(OWNER, "что ты услышал", context(3), position.state)

    assert "пешка белых" in position.speech.text
    assert "что на е два" in repeated.speech.text


async def test_last_reply_can_be_repeated_more_slowly_without_changing_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    position = await conversation.handle(OWNER, "что на е два", context(2), started.state)

    repeated = await conversation.handle(OWNER, "повтори медленно", context(3), position.state)
    heard = await conversation.handle(OWNER, "что ты услышал", context(4), repeated.state)

    assert repeated.speech.text == "Повторяю: На е два — пешка белых."
    assert repeated.speech.tts is not None
    assert "," in repeated.speech.tts
    assert "—," not in repeated.speech.tts
    assert repeated.state.last_reply == position.state.last_reply
    assert "что на е два" in heard.speech.text
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.moves == ()


async def test_clarification_state_can_be_confirmed_on_the_next_request(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        game = GameRepository(session).create_game(OWNER, PlayerColor.WHITE)
    pending = PendingClarification("пешка е два е четыре", ("e2e4",))
    state = ConversationState(game.id, game.revision, clarification=pending)

    reply = await subject(session_factory, offline_settings).handle(OWNER, "да", context(1), state)

    assert reply.turn is not None
    assert reply.turn.player_move == "e2e4"


async def test_new_game_accepts_black_and_engine_level(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    engine = FakeEngine()
    conversation = ConversationService(session_factory, engine, offline_settings)
    reply = await conversation.handle(
        OWNER,
        "новая игра черными уровень 12",
        context(1),
    )

    assert reply.turn is not None
    assert reply.turn.player_color is PlayerColor.BLACK
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(reply.turn.game_id, OWNER)
    assert game.engine.skill_level == 12
    assert engine.skill_levels == [12]
    assert game.moves


async def test_current_engine_level_can_be_asked_in_natural_speech(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))

    reply = await conversation.handle(OWNER, "Какой уровень сложности?", context(2), started.state)

    assert reply.turn is None
    assert reply.speech.text == (
        "Сейчас уровень 7. Шкала — от нуля до двадцати: чем больше число, тем сильнее я играю. "
        "Чтобы изменить уровень, скажите: «уровень пять»."
    )


async def test_new_session_greeting_explains_the_skill_and_next_commands(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    reply = await subject(session_factory, offline_settings).handle(OWNER, "", context(1, new=True))

    assert reply.turn is not None
    assert "Шахматы с Юрой" in reply.speech.text
    assert "шахматы голосом" in reply.speech.text
    assert "пешка е два е четыре" in reply.speech.text
    assert "скажите «помощь»" in reply.speech.text


@pytest.mark.parametrize("utterance", ["помощь", "что ты умеешь"])
async def test_moderation_help_commands_return_an_instruction_in_a_new_session(
    utterance: str,
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    reply = await subject(session_factory, offline_settings).handle(OWNER, utterance, context(1, new=True))

    assert reply.turn is None
    assert "играете в шахматы голосом против компьютера" in reply.speech.text
    assert "новая игра белыми" in reply.speech.text
    assert "пешка е два е четыре" in reply.speech.text


@pytest.mark.parametrize(
    "resume_utterance",
    ["да", "да, давай", "да давай продолжим", "поехали", "погнали", "начали", "начинаем"],
)
async def test_new_session_offers_the_latest_unfinished_game_and_last_two_moves(
    resume_utterance: str,
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        game = repository.create_game(OWNER, PlayerColor.WHITE)
        game = repository.append_moves(game.id, OWNER, game.revision, ("e2e4", "e7e5"))

    conversation = subject(session_factory, offline_settings)
    prompt = await conversation.handle(OWNER, "", context(1, new=True, timezone="Europe/Moscow"))

    assert prompt.turn is None
    assert prompt.state.game_id == game.id
    assert prompt.state.pending_action is not None
    assert prompt.state.pending_action.kind is CommandKind.CONTINUE
    assert "Шахматы с Юрой" in prompt.speech.text
    assert "шахматы голосом" in prompt.speech.text
    assert "скажите «помощь»" in prompt.speech.text.lower()
    assert "Последние два хода" in prompt.speech.text
    assert "пешка e2 e4" in prompt.speech.text
    assert "пешка e7 e5" in prompt.speech.text
    with session_scope(session_factory) as session:
        usage = session.get(UsageRequestRow, request_key("shell", "conversation", "1"))
    assert usage is not None
    assert (usage.release_id, usage.command_kind, usage.routing_outcome) == ("development", "empty", "empty")

    resumed = await conversation.handle(OWNER, resume_utterance, context(2), prompt.state)

    assert resumed.turn is not None
    assert resumed.turn.game_id == game.id


@pytest.mark.parametrize("utterance", ["поехали", "да давай продолжим"])
async def test_resume_answer_is_recorded_as_the_final_confirmation_intent(
    utterance: str,
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        game = GameRepository(session).create_game(OWNER, PlayerColor.WHITE)
    conversation = subject(session_factory, offline_settings)
    prompt = await conversation.handle(OWNER, "", context(1, new=True), ConversationState())

    resumed = await conversation.handle(OWNER, utterance, context(2), prompt.state)

    assert resumed.turn is not None
    assert resumed.turn.game_id == game.id
    with session_scope(session_factory) as session:
        usage = session.get(UsageRequestRow, request_key("shell", "conversation", "2"))
    assert usage is not None
    assert (usage.command_kind, usage.routing_outcome) == ("confirmation", "confirmation")


async def test_start_like_phrase_never_confirms_resignation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    asked = await conversation.handle(OWNER, "сдаюсь", context(2), started.state)

    refused = await conversation.handle(OWNER, "поехали", context(3), asked.state)

    assert refused.speech.text == "Скажите «да» или «нет»."
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.status is GameStatus.ACTIVE


@pytest.mark.parametrize("utterance", ["поехали", "погнали", "начали", "начинаем"])
async def test_start_like_phrase_acknowledges_an_active_game_without_restarting_it(
    utterance: str,
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    reply = await conversation.handle(OWNER, utterance, context(2), started.state)

    assert reply.speech.text == "Хорошо. Ваш ход."
    assert reply.state.pending_action is None
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.moves == ()


async def test_platform_command_ends_only_the_skill_and_preserves_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    handed_off = await conversation.handle(OWNER, "громкость 3", context(2), started.state)

    assert handed_off.end_session is True
    assert handed_off.speech.text == (
        "Партия сохранена. Закрываю навык «Шахматы с Юрой». Теперь повторите команду Алисе."
    )
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.status is GameStatus.ACTIVE
    assert game.moves == ()


async def test_a_platform_command_without_a_game_does_not_promise_a_saved_one(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    reply = await subject(session_factory, offline_settings).handle(OWNER, "включи музыку", context(1))

    assert reply.end_session is True
    assert reply.speech.text == "Закрываю навык «Шахматы с Юрой». Теперь повторите команду Алисе."


async def test_a_farewell_ends_the_session_and_keeps_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    goodbye = await conversation.handle(OWNER, "пока", context(2), started.state)

    assert goodbye.end_session is True
    assert goodbye.speech.text == "До свидания. Партия сохранена."
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.status is GameStatus.ACTIVE


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("алиса", "Слушаю. Ваш ход."),
        ("привет", "Здравствуйте! Ваш ход."),
        ("как тебя зовут", "Я Юра, шахматный помощник Алисы. Ваш ход."),
        ("понятно", "Хорошо. Ваш ход."),
        ("ход", "Что вы хотите: сделать ход, услышать последний ход или открыть помощь?"),
        ("подожди", "Хорошо, подожду. Партия сохранена."),
    ],
)
async def test_short_conversational_turns_are_human_and_do_not_change_the_game(
    utterance: str,
    expected: str,
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    reply = await conversation.handle(OWNER, utterance, context(2), started.state)

    assert reply.speech.text == expected
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.moves == ()


async def test_bare_repeat_replays_the_previous_reply_without_replacing_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    position = await conversation.handle(OWNER, "что на е два", context(2), started.state)

    repeated = await conversation.handle(OWNER, "повтори", context(3), position.state)

    assert repeated.speech.text == position.state.last_reply
    assert repeated.state.last_reply == position.state.last_reply
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.moves == ()


@pytest.mark.parametrize("utterance", ["помощь", "что ты умеешь"])
async def test_help_replaces_a_returning_users_resume_confirmation(
    utterance: str,
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    opened = await conversation.handle(OWNER, "", context(1, new=True))
    prompted = await conversation.handle(OWNER, "", context(2, new=True), ConversationState())

    helped = await conversation.handle(OWNER, utterance, context(3), prompted.state)

    assert "играете в шахматы голосом против компьютера" in helped.speech.text
    assert "пешка е два е четыре" in helped.speech.text
    assert "Скажите «да» или «нет»" not in helped.speech.text
    assert helped.state.pending_action is None
    assert helped.state.help == HelpState(topic=None, page=0)
    assert helped.state.game_id == opened.state.game_id


@pytest.mark.parametrize("utterance", ["е 2 е 4", "да е 2 е 4"])
async def test_a_move_itself_accepts_the_resume_prompt(
    utterance: str,
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        game = GameRepository(session).create_game(OWNER, PlayerColor.WHITE)
    conversation = subject(session_factory, offline_settings)
    prompt = await conversation.handle(OWNER, "", context(1, new=True), ConversationState())

    reply = await conversation.handle(OWNER, utterance, context(2), prompt.state)

    assert reply.turn is not None
    assert reply.turn.game_id == game.id
    assert reply.turn.player_move == "e2e4"
    assert reply.state.pending_action is None


async def test_an_illegal_move_replaces_the_resume_prompt_with_the_real_rule(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        game = GameRepository(session).create_game(OWNER, PlayerColor.WHITE)
    conversation = subject(session_factory, offline_settings)
    prompt = await conversation.handle(OWNER, "", context(1, new=True), ConversationState())

    reply = await conversation.handle(OWNER, "пешка е 2 е 5", context(2), prompt.state)

    assert "Скажите «да» или «нет»" not in reply.speech.text
    assert "недостижимо" in reply.speech.text
    assert reply.state.pending_action is None
    with session_scope(session_factory) as session:
        unchanged = GameRepository(session).load(game.id, OWNER)
    assert unchanged.moves == ()


async def test_puzzle_request_replaces_the_resume_confirmation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        GameRepository(session).create_game(OWNER, PlayerColor.WHITE)
    conversation = subject(session_factory, offline_settings)
    prompt = await conversation.handle(OWNER, "", context(1, new=True), ConversationState())

    reply = await conversation.handle(OWNER, "я хочу в задачи поиграть", context(2), prompt.state)

    assert "Скажите «да» или «нет»" not in reply.speech.text
    assert reply.state.pending_action is None
    assert PuzzleService(session_factory).find_open(OWNER) is not None


async def test_exit_replaces_the_resume_confirmation_without_changing_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        game = GameRepository(session).create_game(OWNER, PlayerColor.WHITE)
    conversation = subject(session_factory, offline_settings)
    prompt = await conversation.handle(OWNER, "", context(1, new=True), ConversationState())

    reply = await conversation.handle(OWNER, "выключи навык", context(2), prompt.state)

    assert reply.end_session is True
    assert reply.state.pending_action is None
    with session_scope(session_factory) as session:
        unchanged = GameRepository(session).load(game.id, OWNER)
    assert unchanged.status is GameStatus.ACTIVE
    assert unchanged.moves == ()


async def test_new_session_greeting_explains_the_skill_when_resuming_a_puzzle(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    await conversation.handle(OWNER, "дай задачу", context(1, new=True))

    prompted = await conversation.handle(OWNER, "", context(2, new=True), ConversationState())

    assert "Шахматы с Юрой" in prompted.speech.text
    assert "шахматы голосом" in prompted.speech.text
    assert "нерешенная задача" in prompted.speech.text
    assert "скажите «помощь»" in prompted.speech.text.lower()
    assert prompted.state.pending_action is not None
    assert prompted.state.pending_action.kind is CommandKind.PUZZLE


async def test_bare_dont_know_reveals_the_solution_only_inside_a_puzzle(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    offered = await conversation.handle(OWNER, "дай задачу", context(1))

    solved = await conversation.handle(OWNER, "не знаю", context(2), offered.state)

    assert solved.speech.text.startswith("Решение:")
    assert PuzzleService(session_factory).find_open(OWNER) is None


async def test_unplayed_game_is_not_described_as_played_today(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        game = GameRepository(session).create_game(OWNER, PlayerColor.WHITE)

    prompt = await subject(session_factory, offline_settings).handle(OWNER, "", context(1, new=True))

    assert prompt.state.game_id == game.id
    assert "еще не сделали ход" in prompt.speech.text
    assert "сегодня" not in prompt.speech.text


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("ноль", 0),
        ("один", 1),
        ("два", 2),
        ("три", 3),
        ("четыре", 4),
        ("пять", 5),
        ("шесть", 6),
        ("семь", 7),
        ("восемь", 8),
        ("девять", 9),
        ("десять", 10),
        ("одиннадцать", 11),
        ("двенадцать", 12),
        ("тринадцать", 13),
        ("четырнадцать", 14),
        ("пятнадцать", 15),
        ("шестнадцать", 16),
        ("семнадцать", 17),
        ("восемнадцать", 18),
        ("девятнадцать", 19),
        ("двадцать", 20),
        ("нуль", 0),
    ],
)
async def test_new_game_accepts_spoken_engine_levels(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    spoken: str,
    expected: int,
) -> None:
    engine = FakeEngine()
    reply = await ConversationService(session_factory, engine, offline_settings).handle(
        OWNER,
        f"новая игра черными уровень {spoken}",
        context(expected + 1),
    )

    assert reply.turn is not None
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(reply.turn.game_id, OWNER)
    assert game.engine.skill_level == expected
    assert engine.skill_levels == [expected]


async def test_resignation_requires_confirmation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    asked = await conversation.handle(OWNER, "сдаюсь", context(2), started.state)
    cancelled = await conversation.handle(OWNER, "нет", context(3), asked.state)

    assert asked.turn is None
    assert asked.state.pending_action is not None
    assert cancelled.state.pending_action is None
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.status is GameStatus.ACTIVE

    asked_again = await conversation.handle(OWNER, "сдаюсь", context(4), cancelled.state)
    confirmed = await conversation.handle(OWNER, "да", context(5), asked_again.state)

    assert confirmed.turn is not None
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert game.status is GameStatus.RESIGNED


async def test_new_game_confirmation_preserves_requested_settings(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    asked = await conversation.handle(
        OWNER,
        # Spelled with the ё the speaker actually uses: the colour is read off the
        # normalised text, so folding it is what makes this game a black one.
        "новая игра чёрными уровень двенадцать",
        context(2),
        started.state,
    )
    confirmed = await conversation.handle(OWNER, "да", context(3), asked.state)

    assert asked.turn is None
    assert confirmed.turn is not None
    assert confirmed.turn.game_id != started.state.game_id
    assert confirmed.turn.player_color is PlayerColor.BLACK
    with session_scope(session_factory) as session:
        previous = GameRepository(session).load(started.state.game_id or "", OWNER)
        game = GameRepository(session).load(confirmed.turn.game_id, OWNER)
    assert previous.status is GameStatus.RESIGNED
    assert game.engine.skill_level == 12


async def test_help_before_a_game_offers_topics_without_starting_anything(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, "справка", context(1))

    assert "играете в шахматы голосом против компьютера" in reply.speech.text
    assert "Разделы справки" in reply.speech.text
    assert "Разделы справки. Правила, ходы, позиция, факты." in reply.speech.spoken()
    assert "Партия, настройки, тренер, разбор, задачи, речь." in reply.speech.spoken()
    assert "Назовите раздел. Или скажите: «все команды»." in reply.speech.spoken()
    assert reply.state.help == HelpState(topic=None, page=0)
    assert reply.state.game_id is None
    assert reply.turn is None


async def test_help_uses_short_sentences_for_tts_intonation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, "справка по ходам", context(1))

    spoken = reply.speech.spoken()
    assert "Ход можно назвать так. Например:" in spoken
    assert "ответьте «да» или «нет». Либо назовите ход точнее." in spoken


@pytest.mark.parametrize(
    ("utterance", "expected", "phrase"),
    [
        ("справка про правила", HelpTopic.RULES, "поставить мат королю соперника"),
        ("справка по ходам", HelpTopic.MOVES, "«пешка е два е четыре»"),
        ("справка по позиции", HelpTopic.POSITION, "две горизонтали"),
        ("справка про факты", HelpTopic.FACTS, "за кого я играю"),
        ("справка про партию", HelpTopic.GAME, "уровень десять"),
        ("справка про настройки", HelpTopic.SETTINGS, "говори кратко"),
        ("справка про тренера", HelpTopic.TRAINING, "режим тренера"),
        ("справка про разбор", HelpTopic.REVIEW, "разбери партию"),
        ("справка про задачи", HelpTopic.PUZZLES, "дай задачу"),
        ("справка про речь", HelpTopic.SPEECH, "что ты услышал"),
        ("все команды", HelpTopic.ALL, "Все команды."),
    ],
)
async def test_every_help_section_can_be_asked_for_by_name(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
    expected: HelpTopic,
    phrase: str,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, utterance, context(1))

    assert reply.state.help == HelpState(topic=expected, page=0)
    assert phrase in reply.speech.text


@pytest.mark.parametrize(
    "utterance",
    [
        "какие у тебя есть задачи",
        "какие задачи у тебя есть",
        "какие бывают шахматные задачи",
        "какие виды задач у тебя есть",
        "какие типы задач доступны",
        "какие категории задач есть",
        "темы шахматных задач",
        "расскажи про задачи",
        "что за задачи у тебя есть",
        "какие у тебя есть головоломки",
        "у тебя какие задачи",
        "есть какие нибудь задачи",
        "на какие темы есть задачи",
        "по каким темам можно порешать",
        "что можно порешать",
        "какие задачи ты можешь предложить",
    ],
)
async def test_natural_puzzle_catalogue_questions_open_help_without_starting_a_puzzle(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, utterance, context(1))

    assert reply.state.help == HelpState(topic=HelpTopic.PUZZLES, page=0)
    assert "Темы:" in reply.speech.text
    assert "мат в два хода, вилка, связка и сквозной удар" in reply.speech.text
    assert reply.state.game_id is None
    assert reply.turn is None


async def test_rules_help_is_read_only_paged_and_replay_safe_during_a_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    with session_scope(session_factory) as session:
        before = GameRepository(session).load(started.state.game_id or "", OWNER)
    request = context(2)

    first = await conversation.handle(OWNER, "расскажи правила шахмат", request, started.state)
    replayed = await conversation.handle(OWNER, "расскажи правила шахмат", request, started.state)
    second = await conversation.handle(OWNER, "дальше", context(3), first.state)

    with session_scope(session_factory) as session:
        after = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert first.speech == replayed.speech
    assert first.state.help == HelpState(topic=HelpTopic.RULES, page=0)
    assert second.state.help == HelpState(topic=HelpTopic.RULES, page=1)
    assert (after.moves, after.revision, after.pending_engine_turn) == (
        before.moves,
        before.revision,
        before.pending_engine_turn,
    )


async def test_puzzle_catalogue_help_is_read_only_and_replay_safe_during_a_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    with session_scope(session_factory) as session:
        before = GameRepository(session).load(started.state.game_id or "", OWNER)
    request = context(2)

    first = await conversation.handle(OWNER, "что можно порешать", request, started.state)
    replayed = await conversation.handle(OWNER, "что можно порешать", request, started.state)

    with session_scope(session_factory) as session:
        after = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert first.speech == replayed.speech
    assert first.state.help == HelpState(topic=HelpTopic.PUZZLES, page=0)
    assert (after.moves, after.revision, after.pending_engine_turn) == (
        before.moves,
        before.revision,
        before.pending_engine_turn,
    )


async def test_puzzle_catalogue_question_works_after_declining_an_unfinished_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        repository.create_game(OWNER, PlayerColor.WHITE)

    conversation = subject(session_factory, offline_settings)
    prompt = await conversation.handle(OWNER, "", context(1, new=True))
    declined = await conversation.handle(OWNER, "нет", context(2), prompt.state)

    reply = await conversation.handle(OWNER, "какие у тебя есть задачи", context(3), declined.state)

    assert declined.speech.text == "Хорошо. Скажите «новая игра», если хотите начать другую."
    assert reply.state.help == HelpState(topic=HelpTopic.PUZZLES, page=0)
    assert "Темы:" in reply.speech.text
    assert reply.turn is None


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("что еще ты умеешь", CommandKind.HELP),
        ("расскажи о возможностях", CommandKind.HELP),
        ("что ты можешь", CommandKind.HELP),
        ("давай сыграем", CommandKind.START),
        ("хочу играть", CommandKind.START),
        ("вернемся к игре", CommandKind.CONTINUE),
        ("закончим на сегодня", CommandKind.EXIT),
        ("какие у меня фигуры", CommandKind.POSITION_QUERY),
        ("не так подробно", CommandKind.PREFERENCE),
        ("можно помедленнее", CommandKind.PREFERENCE),
        ("разверни доску", CommandKind.ORIENTATION_QUERY),
        ("вернись", CommandKind.NAVIGATE_BACK),
        ("отмена", CommandKind.CANCEL_CLARIFY),
        ("я передумал", CommandKind.CANCEL_CLARIFY),
        ("я не это имел в виду", CommandKind.CANCEL_CLARIFY),
        ("что лучше сыграть", CommandKind.TRAINING),
        ("объясни свой ход", CommandKind.TRAINING),
        ("как я сыграл", CommandKind.REVIEW),
        ("давай разберем игру", CommandKind.REVIEW),
        ("покажи мои ошибки", CommandKind.REVIEW),
        ("где я играл плохо", CommandKind.REVIEW),
    ],
)
def test_natural_non_puzzle_phrases_have_explicit_intents(utterance: str, expected: CommandKind) -> None:
    assert route(utterance, chess.Board()).kind is expected


def test_natural_trainer_and_review_phrases_keep_their_specific_questions() -> None:
    candidates = route("что лучше сыграть", chess.Board())
    explanation = route("объясни свой ход", chess.Board())
    summary = route("как я сыграл", chess.Board())
    mistake = route("где я играл плохо", chess.Board())

    assert candidates.training is not None and candidates.training.question is TrainingQuestion.CANDIDATES
    assert explanation.training is not None and explanation.training.question is TrainingQuestion.WHY_MOVE
    assert summary.review is not None and summary.review.question is ReviewQuestion.SUMMARY
    assert mistake.review is not None and mistake.review.question is ReviewQuestion.MAIN_MISTAKE


@pytest.mark.parametrize("utterance", ["что еще ты умеешь", "расскажи о возможностях", "что ты можешь"])
async def test_natural_capability_questions_open_the_help_menu(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, utterance, context(1))

    assert "Разделы справки" in reply.speech.text
    assert reply.state.help == HelpState(topic=None, page=0)
    assert reply.state.game_id is None


@pytest.mark.parametrize("utterance", ["давай сыграем", "хочу играть"])
async def test_natural_start_phrases_begin_a_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, utterance, context(1))

    assert "Новая партия" in reply.speech.text
    assert reply.state.game_id is not None


async def test_natural_continue_and_exit_phrases_preserve_the_saved_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    resumed = await conversation.handle(OWNER, "вернемся к игре", context(2), ConversationState())
    ended = await conversation.handle(OWNER, "закончим на сегодня", context(3), resumed.state)

    assert resumed.state.game_id == started.state.game_id
    assert ended.end_session is True
    with session_scope(session_factory) as session:
        stored = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert stored.status is GameStatus.ACTIVE


async def test_elliptical_settings_and_orientation_clarification_are_safe(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    with session_scope(session_factory) as session:
        before = GameRepository(session).load(started.state.game_id or "", OWNER)

    brief = await conversation.handle(OWNER, "не так подробно", context(2), started.state)
    slow = await conversation.handle(OWNER, "можно помедленнее", context(3), brief.state)
    asked = await conversation.handle(OWNER, "разверни доску", context(4), slow.state)
    oriented = await conversation.handle(OWNER, "за черных", context(5), asked.state)

    assert brief.preferences is not None and brief.preferences.detail_level is DetailLevel.BRIEF
    assert slow.preferences is not None and slow.preferences.pause_style is PauseStyle.EXTENDED
    assert asked.speech.text == "Как показать доску: за белых или за черных?"
    assert oriented.preferences is not None and oriented.preferences.board_orientation is BoardOrientation.BLACK
    with session_scope(session_factory) as session:
        after = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert (after.moves, after.revision) == (before.moves, before.revision)


@pytest.mark.parametrize("utterance", ["отмена", "я передумал", "я не это имел в виду"])
async def test_natural_cancellation_phrases_decline_pending_resignation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    prompted = await conversation.handle(OWNER, "сдаюсь", context(2), started.state)

    cancelled = await conversation.handle(OWNER, utterance, context(3), prompted.state)

    assert cancelled.speech.text == "Хорошо, отменяю."
    with session_scope(session_factory) as session:
        stored = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert stored.status is GameStatus.ACTIVE


async def test_back_and_rotate_never_undo_a_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    played = await conversation.handle(OWNER, "пешка е четыре", context(2), started.state)
    with session_scope(session_factory) as session:
        before = GameRepository(session).load(started.state.game_id or "", OWNER)

    rotated = await conversation.handle(OWNER, "разверни доску", context(3), played.state)
    backed = await conversation.handle(OWNER, "вернись", context(4), rotated.state)

    assert "за белых или за черных" in rotated.speech.text
    assert backed.speech.text == "Куда вернуться: к партии, выйти из задач или закрыть справку?"
    with session_scope(session_factory) as session:
        after = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert (after.moves, after.revision) == (before.moves, before.revision)
    assert route("отмени ход", chess.Board()).kind is CommandKind.UNDO
    assert route("верни последний ход", chess.Board()).kind is CommandKind.UNDO


async def test_natural_trainer_and_review_questions_reach_their_services(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    trainer = await conversation.handle(OWNER, "что лучше сыграть", context(2), started.state)
    review = await conversation.handle(OWNER, "как я сыграл", context(3), trainer.state)

    assert "Включить режим тренера" in trainer.speech.text
    assert review.speech.text == "Сейчас партия еще идет. Доиграйте ее, потом скажите «разбери партию»."


async def test_the_word_razbor_opens_the_help_topic_while_help_is_open(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """The same word is a review request on its own and a topic name inside the help."""
    conversation = subject(session_factory, offline_settings)
    opened = await conversation.handle(OWNER, "все команды", context(1))

    inside = await conversation.handle(OWNER, "разбор", context(2), opened.state)
    outside = await conversation.handle(OWNER, "разбор", context(3), None)

    assert inside.state.help == HelpState(topic=HelpTopic.REVIEW, page=0)
    assert "разбери партию" in inside.speech.text
    assert "Законченной партии еще нет" not in inside.speech.text
    assert outside.state.help is None
    assert "Законченной партии еще нет" in outside.speech.text


async def test_asking_to_enlarge_the_board_reads_the_position_instead(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """A low-vision player gets the board read out, not another command to say."""
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    reply = await conversation.handle(OWNER, "увеличь вижу плохо", context(2), started.state)

    assert reply.speech.text.startswith("На весь экран переключить не могу. Читаю доску.")
    assert len(reply.speech.text) > len("На весь экран переключить не могу. Читаю доску.")
    assert reply.card is None
    assert reply.state.game_id == started.state.game_id


async def test_asking_to_play_by_tapping_names_the_spoken_move_instead(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    reply = await conversation.handle(OWNER, "алиса а можно играть не голосом а визуально", context(2), started.state)

    assert reply.speech.text == (
        "Нажимать на клетки здесь нельзя, ходы я принимаю только голосом. Скажите, какая фигура и на какую клетку идет."
    )


async def test_asking_to_enlarge_the_board_during_a_puzzle_reads_the_puzzle(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """The open puzzle owns the board; the saved game must not be read instead."""
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    playing = await conversation.handle(OWNER, "плохо вижу доску", context(2), started.state)
    offered = await conversation.handle(OWNER, "дай задачу", context(3), playing.state)

    reply = await conversation.handle(OWNER, "плохо вижу доску", context(4), offered.state)

    assert reply.speech.text.startswith("На весь экран переключить не могу. Читаю доску.")
    assert reply.speech.text != playing.speech.text
    assert reply.card is not None
    assert reply.card.title == "Задача"


async def test_the_board_reading_continues_page_by_page_after_a_visibility_request(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    first = await conversation.handle(OWNER, "увеличь вижу плохо", context(2), started.state)
    second = await conversation.handle(OWNER, "дальше", context(3), first.state)

    assert first.state.position_page == 0
    assert second.state.position_page == 1
    assert second.speech.text != first.speech.text


async def test_a_visibility_request_answers_a_waiting_confirmation_with_the_board(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    asked = await conversation.handle(OWNER, "новая игра", context(2), started.state)

    reply = await conversation.handle(OWNER, "плохо вижу доску", context(3), asked.state)

    assert asked.state.pending_action is not None
    assert reply.state.pending_action is None
    assert reply.speech.text.startswith("На весь экран переключить не могу.")


async def test_asking_to_play_by_tapping_without_a_game_starts_nothing(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    reply = await subject(session_factory, offline_settings).handle(OWNER, "только визуально", context(1))

    assert reply.speech.text == (
        "Нажимать на клетки здесь нельзя, ходы я принимаю только голосом. Скажите «новая игра», и начнем."
    )
    assert reply.state.game_id is None


async def test_asking_to_enlarge_the_board_without_a_game_offers_one(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    reply = await subject(session_factory, offline_settings).handle(OWNER, "увеличь картинку", context(1))

    assert "новая игра" in reply.speech.text


async def test_help_navigation_walks_the_catalogue_forward_back_and_to_the_start(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    opened = await conversation.handle(OWNER, "все команды", context(1))
    assert "скажите: «дальше»" in opened.speech.text.lower()

    forward = await conversation.handle(OWNER, "дальше", context(2), opened.state)
    assert forward.state.help == HelpState(topic=HelpTopic.ALL, page=1)

    back = await conversation.handle(OWNER, "назад", context(3), forward.state)
    assert back.state.help == HelpState(topic=HelpTopic.ALL, page=0)

    restarted = await conversation.handle(OWNER, "дальше", context(4), back.state)
    restarted = await conversation.handle(OWNER, "сначала", context(5), restarted.state)
    assert restarted.state.help == HelpState(topic=HelpTopic.ALL, page=0)
    assert restarted.speech.text == back.speech.text


async def test_unknown_help_topic_lists_the_real_sections_and_keeps_help_open(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, "справка про погоду", context(1))

    assert "Такого раздела в справке нет" in reply.speech.text
    assert "позиция" in reply.speech.text
    assert reply.state.help == HelpState(topic=None, page=0)


async def test_leaving_help_closes_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    opened = await conversation.handle(OWNER, "справка по ходам", context(1))

    reply = await conversation.handle(OWNER, "закрой справку", context(2), opened.state)

    assert "Закрываю справку" in reply.speech.text
    assert reply.state.help is None


async def test_help_inside_a_game_changes_neither_the_game_nor_the_revision(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    played = await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)

    helped = await conversation.handle(OWNER, "что ты умеешь", context(3), played.state)
    paged = await conversation.handle(OWNER, "дальше", context(4), helped.state)

    assert "Разделы справки" in helped.speech.text
    assert helped.turn is None and paged.turn is None
    assert paged.state.revision == played.state.revision
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(played.state.game_id or "", OWNER)
    assert played.turn is not None
    assert state.moves == (played.turn.player_move, played.turn.engine_move)
    assert state.revision == played.state.revision
    assert state.pending_engine_turn is None


async def test_next_page_still_reads_the_board_when_help_is_closed(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    read = await conversation.handle(OWNER, "какая позиция", context(2), started.state)
    more = await conversation.handle(OWNER, "дальше", context(3), read.state)

    assert read.state.help is None
    assert more.state.position_page == 1
    assert "горизонталь" in more.speech.text


async def test_a_section_named_alone_after_the_menu_opens_that_section(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    menu = await conversation.handle(OWNER, "справка", context(1))

    reply = await conversation.handle(OWNER, "ходы", context(2), menu.state)

    assert reply.state.help == HelpState(topic=HelpTopic.MOVES, page=0)
    assert reply.state.game_id is None


async def test_a_board_question_during_help_still_reads_the_board(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    menu = await conversation.handle(OWNER, "справка", context(2), started.state)

    reply = await conversation.handle(OWNER, "какая позиция", context(3), menu.state)

    assert reply.state.help is None
    assert "горизонталь" in reply.speech.text


async def test_help_after_a_finished_game_still_returns_the_instruction(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    asked = await conversation.handle(OWNER, "сдаюсь", context(2), started.state)
    resigned = await conversation.handle(OWNER, "да", context(3), asked.state)

    reply = await conversation.handle(OWNER, "справка", context(4), resigned.state)

    assert "играете в шахматы голосом против компьютера" in reply.speech.text
    assert "Разделы справки" in reply.speech.text


# One phrase per public command, so a command that is neither advertised nor
# advertised twice fails the audit. Every implemented category is listed here.
_CATEGORY_PHRASES = (
    # Moves, board questions and the game itself.
    "пешка е два е четыре",
    "отмени ход",
    "какая позиция",
    "что на е четыре",
    "где белые слоны",
    "чей ход",
    "есть ли шах",
    "какой был последний ход",
    "новая игра черными",
    "продолжить последнюю партию",
    "предлагаю ничью",
    "сдаюсь",
    "какой уровень",
    "реванш",
    # Facts about the game.
    "за кого я играю",
    "какой сейчас ход",
    "сколько ходов мы сыграли",
    "какие фигуры съедены",
    "могу ли я сделать рокировку",
    "кто дает шах",
    "какой дебют",
    "какая стадия партии",
    "что изменил последний ход",
    # Settings.
    "говори кратко",
    "говори обычно",
    "говори подробно",
    "говори медленнее",
    "говори быстрее",
    "короткая нотация",
    "полная нотация",
    "доска всегда за белых",
    # Trainer.
    "включи режим тренера",
    "выключи тренера",
    "оцени позицию",
    "назови оценку числом",
    "почему ты так сходил",
    "чем ты угрожаешь",
    "какие ходы хорошие",
    "что будет, если я сыграю",
    "подскажи",
    "где я ошибся",
    "оставить мой ход",
    # Review and PGN.
    "разбери партию",
    "продолжить разбор",
    "где перелом",
    "главная ошибка",
    "сколько раз я ошибся",
    "сколько ошибок я сделал",
    "сколько у меня ошибок",
    "продиктуй ходы",
    "покажи pgn",
    "сыграть эту позицию заново",
    "выйти из разбора",
    # Puzzles.
    "дай задачу",
    "задача на мат в один ход",
    "следующая задача",
    "покажи решение",
    "какая у меня серия",
    "вернуться к партии",
    # Speech.
    "что ты услышал",
    "повтори медленно",
    "повтори координаты по буквам",
)


@pytest.mark.parametrize("phrase", _CATEGORY_PHRASES)
def test_every_public_command_category_lives_in_exactly_one_help_section(phrase: str) -> None:
    holders = [section.topic for section in SECTIONS if phrase in " ".join(section.lines).lower().replace("ё", "е")]

    assert len(holders) == 1, f"«{phrase}» is listed in {holders}"


@pytest.mark.parametrize("phrase", _CATEGORY_PHRASES)
def test_every_advertised_command_is_understood_by_the_router(phrase: str) -> None:
    """Help may only advertise phrases the router actually recognises."""
    routed = route(phrase, board=chess.Board())

    assert routed.kind is not CommandKind.UNKNOWN, f"«{phrase}» is advertised but not routed"


async def test_the_whole_catalogue_stays_paged_after_the_new_sections(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    reply = await conversation.handle(OWNER, "все команды", context(1))

    pages = 1
    while "скажите: «дальше»" in reply.speech.text.lower():
        reply = await conversation.handle(OWNER, "дальше", context(pages + 1), reply.state)
        pages += 1

    assert pages > 1
    assert reply.state.help == HelpState(topic=HelpTopic.ALL, page=pages - 1)
    assert "Это конец раздела" in reply.speech.text


@pytest.mark.parametrize(
    ("topic", "note"),
    [
        ("справка про тренера", "сначала скажите «новая игра»"),
        ("справка про разбор", "сыграйте партию до конца"),
        ("справка про факты", "после «новая игра»"),
    ],
)
async def test_a_section_says_what_its_commands_need_before_a_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    topic: str,
    note: str,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, topic, context(1))

    assert note in reply.speech.text


async def test_an_open_game_changes_what_the_trainer_and_review_sections_advise(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    trainer = await conversation.handle(OWNER, "справка про тренера", context(2), started.state)
    review = await conversation.handle(OWNER, "справка про разбор", context(3), trainer.state)

    assert "включи режим тренера" in trainer.speech.text
    assert "когда партия закончится" in review.speech.text


async def test_help_inside_a_puzzle_reads_the_instruction_and_leaves_the_attempt_alone(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    offered = await conversation.handle(OWNER, "дай задачу", context(1, new=True))
    before = PuzzleService(session_factory).find_open(OWNER)
    assert before is not None

    helped = await conversation.handle(OWNER, "справка", context(2), offered.state)
    paged = await conversation.handle(OWNER, "дальше", context(3), helped.state)
    closed = await conversation.handle(OWNER, "закрой справку", context(4), paged.state)

    assert "Разделы справки" in helped.speech.text
    assert paged.state.help == HelpState(topic=HelpTopic.ALL, page=0)
    assert closed.state.help is None
    after = PuzzleService(session_factory).find_open(OWNER)
    assert after is not None
    assert after.puzzle.id == before.puzzle.id
    assert after.attempt.node == before.attempt.node
    assert after.attempt.mistakes == before.attempt.mistakes
    assert after.attempt.hints == before.attempt.hints
    assert after.attempt.revision == before.attempt.revision


async def test_help_navigation_leaves_an_open_review_where_it_stopped(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    asked = await conversation.handle(OWNER, "сдаюсь", context(2), started.state)
    resigned = await conversation.handle(OWNER, "да", context(3), asked.state)
    dictated = await conversation.handle(OWNER, "продиктуй ходы", context(4), resigned.state)
    game_id = dictated.state.game_id or ""
    with session_scope(session_factory) as session:
        before = ReviewRepository(session).find(game_id, OWNER)
    assert before is not None

    helped = await conversation.handle(OWNER, "справка про разбор", context(5), dictated.state)
    paged = await conversation.handle(OWNER, "дальше", context(6), helped.state)

    assert paged.state.help == HelpState(topic=HelpTopic.REVIEW, page=1)
    with session_scope(session_factory) as session:
        after = ReviewRepository(session).find(game_id, OWNER)
    assert after == before


async def test_help_topic_navigation_is_recorded_as_help_after_contextual_interpretation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    helped = await conversation.handle(OWNER, "помощь", context(2), started.state)

    topic = await conversation.handle(OWNER, "партия", context(3), helped.state)

    assert topic.state.help is not None
    with session_scope(session_factory) as session:
        usage = session.get(UsageRequestRow, request_key("shell", "conversation", "3"))
        transcript = session.scalars(
            select(AsrTranscriptRow).where(AsrTranscriptRow.request_key == request_key("shell", "conversation", "3"))
        ).one()
    assert usage is not None
    assert (usage.command_kind, usage.routing_outcome) == ("help", "handled")
    assert transcript.outcome == "help"


async def test_review_navigation_is_recorded_as_review_after_contextual_interpretation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    asked = await conversation.handle(OWNER, "сдаюсь", context(2), started.state)
    resigned = await conversation.handle(OWNER, "да", context(3), asked.state)
    dictated = await conversation.handle(OWNER, "продиктуй ходы", context(4), resigned.state)

    await conversation.handle(OWNER, "назад", context(5), dictated.state)

    with session_scope(session_factory) as session:
        usage = session.get(UsageRequestRow, request_key("shell", "conversation", "5"))
    assert usage is not None
    assert (usage.command_kind, usage.routing_outcome) == ("review", "handled")


async def test_the_puzzle_section_points_back_to_the_game_while_a_puzzle_is_open(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    offered = await conversation.handle(OWNER, "дай задачу", context(1, new=True))

    reply = await conversation.handle(OWNER, "справка про партию", context(2), offered.state)

    assert "вернуться к партии" in reply.speech.text
    assert PuzzleService(session_factory).find_open(OWNER) is not None


@pytest.mark.parametrize(
    ("utterance", "phrase"),
    [
        ("за кого я играю", "Вы играете белыми"),
        ("какой сейчас ход", "-й ход"),
        ("сколько ходов сыграно", "Сыграно"),
        ("какие фигуры съедены", "снял"),
        ("могу ли я рокироваться", "Короткая рокировка"),
        ("кто дает шах", "шаха нет"),
        ("что изменил последний ход", "Изменения:"),
    ],
)
async def test_game_facts_are_answered_without_touching_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
    phrase: str,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    played = await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)

    reply = await conversation.handle(OWNER, utterance, context(3), played.state)

    assert phrase in reply.speech.text
    assert reply.turn is None
    assert reply.state.revision == played.state.revision
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(played.state.game_id or "", OWNER)
    assert played.turn is not None
    assert state.moves == (played.turn.player_move, played.turn.engine_move)
    assert state.revision == played.state.revision
    assert state.pending_engine_turn is None


async def test_a_castling_question_is_never_played_as_a_castling_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    played = await conversation.handle(OWNER, "конь же один эф три", context(2), started.state)

    reply = await conversation.handle(OWNER, "возможна ли рокировка", context(3), played.state)

    assert reply.turn is None
    assert "рокировка" in reply.speech.text
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(played.state.game_id or "", OWNER)
    assert len(state.moves) == 2


async def test_a_game_fact_before_any_game_does_not_start_one(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, "за кого я играю", context(1))

    assert "Партии еще нет" in reply.speech.text
    assert reply.state.game_id is None
    assert reply.turn is None
    with session_scope(session_factory) as session:
        assert GameRepository(session).find_latest_active(OWNER) is None


async def test_the_plain_check_question_still_reads_the_position(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    reply = await conversation.handle(OWNER, "есть ли шах", context(2), started.state)

    assert reply.speech.text == "Сейчас шаха нет."


async def _finished_game(
    conversation: ConversationService,
    opening: str,
    first_message: int,
) -> ConversationState:
    """Start the described game and resign it, so a rematch has a game to answer."""
    started = await conversation.handle(OWNER, opening, context(first_message))
    asked = await conversation.handle(OWNER, "сдаюсь", context(first_message + 1), started.state)
    resigned = await conversation.handle(OWNER, "да", context(first_message + 2), asked.state)
    assert resigned.turn is not None
    return resigned.state


async def test_the_new_game_offered_when_the_game_ends_starts_without_another_question(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    finished = await _finished_game(conversation, "новая игра", 1)

    started = await conversation.handle(OWNER, "новая игра", context(4), finished)

    assert "Новая партия." in started.speech.text
    assert started.state.game_id != finished.game_id


async def test_a_new_game_while_one_is_running_still_asks_to_end_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    running = await conversation.handle(OWNER, "новая игра", context(1))

    asked = await conversation.handle(OWNER, "новая игра", context(2), running.state)

    assert asked.speech.text == "Начать новую партию и закончить текущую? Скажите «да» или «нет»."
    assert asked.state.game_id == running.state.game_id


async def test_settings_command_is_stored_and_never_played_as_a_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    played = await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)

    reply = await conversation.handle(OWNER, "называй только клетку назначения", context(3), played.state)

    assert reply.turn is None
    assert reply.preferences is not None
    assert reply.preferences.notation_style is NotationStyle.SHORT
    assert reply.state.revision == played.state.revision
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(played.state.game_id or "", OWNER)
    assert len(state.moves) == 2
    assert state.pending_engine_turn is None


async def test_short_notation_applies_to_the_next_engine_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    await conversation.handle(OWNER, "короткая аннотация", context(2), started.state)

    reply = await conversation.handle(OWNER, "пешка е два е четыре", context(3), started.state)

    assert reply.turn is not None
    engine_move = reply.turn.engine_move or ""
    assert f" {engine_move[2:4]}." in reply.speech.text
    assert engine_move[:2] not in reply.speech.text


async def test_full_notation_restores_both_squares_after_the_short_one(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    await conversation.handle(OWNER, "короткая аннотация", context(2), started.state)
    await conversation.handle(OWNER, "полная аннотация", context(3), started.state)

    reply = await conversation.handle(OWNER, "пешка е два е четыре", context(4), started.state)

    assert reply.turn is not None
    engine_move = reply.turn.engine_move or ""
    assert engine_move[:2] in reply.speech.text
    assert engine_move[2:4] in reply.speech.text


async def test_slow_adds_pauses_and_fast_removes_only_those(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    slow = await conversation.handle(OWNER, "говори медленнее", context(2), started.state)
    slow_answer = await conversation.handle(OWNER, "какой уровень", context(3), slow.state)
    fast = await conversation.handle(OWNER, "говори быстрее", context(4), slow_answer.state)
    fast_answer = await conversation.handle(OWNER, "какой уровень", context(5), fast.state)

    assert PAUSE_MARKUP in slow_answer.speech.spoken()
    assert slow_answer.speech.text == fast_answer.speech.text
    assert PAUSE_MARKUP not in fast_answer.speech.spoken()
    # «Быстрее» drops only the pauses the skill added, never the punctuation.
    assert fast_answer.speech.text.endswith(".")


async def test_detail_preference_shortens_or_extends_only_the_advice(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    await conversation.handle(OWNER, "говори кратко", context(2), started.state)
    brief = await conversation.handle(OWNER, "какой уровень", context(3), started.state)
    await conversation.handle(OWNER, "говори подробнее", context(4), brief.state)
    detailed = await conversation.handle(OWNER, "какой уровень", context(5), brief.state)
    detailed_move = await conversation.handle(OWNER, "пешка е два е четыре", context(6), detailed.state)

    assert "Сейчас уровень" in brief.speech.text
    assert "Чтобы изменить уровень" not in brief.speech.text
    assert "Чтобы изменить уровень" in detailed.speech.text
    assert detailed_move.speech.text.endswith("Сейчас ваш ход.")


async def test_normal_detail_uses_natural_command_and_confirmation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "говори кратко", context(1))

    reply = await conversation.handle(OWNER, "говори обычно", context(2), started.state)

    assert reply.preferences is not None
    assert reply.preferences.detail_level is DetailLevel.NORMAL
    assert reply.speech.text == "Буду отвечать как обычно."


async def test_orientation_preference_survives_a_new_session(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра черными", context(1))

    await conversation.handle(OWNER, "доску всегда белыми", context(2), started.state)
    later = await conversation.handle(OWNER, "есть ли шах", context(3), ConversationState())

    assert later.preferences is not None
    assert later.preferences.board_orientation is BoardOrientation.WHITE
    assert later.preferences.orientation_for(PlayerColor.BLACK) is PlayerColor.WHITE


async def test_preferences_are_isolated_per_owner(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    await conversation.handle(OWNER, "говори кратко", context(1))

    other = await conversation.handle("d" * 64, "есть ли шах", context(2))

    assert other.preferences is not None
    assert other.preferences.detail_level is DetailLevel.NORMAL


async def test_a_first_preference_deadlock_is_retried(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = subject(session_factory, offline_settings)
    original = PreferencesRepository.load
    attempts = 0

    def deadlock_once(repository: PreferencesRepository, owner_key: str, for_update: bool = False):  # noqa: ANN202
        nonlocal attempts
        if for_update and attempts == 0:
            attempts += 1
            raise OperationalError("INSERT", {}, Exception(1213, "deadlock"))
        return original(repository, owner_key, for_update)

    monkeypatch.setattr(PreferencesRepository, "load", deadlock_once)

    reply = await conversation.handle(OWNER, "говори кратко", context(1))

    assert reply.preferences is not None
    assert reply.preferences.detail_level is DetailLevel.BRIEF
    assert attempts == 1


async def test_rematch_keeps_the_colour_and_level_of_the_finished_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    finished = await _finished_game(conversation, "новая игра черными уровень двенадцать", 1)

    reply = await conversation.handle(OWNER, "реванш", context(4), finished)

    assert reply.turn is not None
    assert reply.turn.player_color is PlayerColor.BLACK
    assert reply.turn.game_id != finished.game_id
    assert "Реванш. Вы играете черными, уровень 12." in reply.speech.text
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(reply.turn.game_id, OWNER)
    assert state.engine.skill_level == 12


async def test_rematch_can_swap_the_colour_and_raise_the_level_within_the_cap(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    finished = await _finished_game(conversation, "новая игра белыми уровень девятнадцать", 1)

    reply = await conversation.handle(OWNER, "реванш другим цветом и сложнее", context(4), finished)

    assert reply.turn is not None
    assert reply.turn.player_color is PlayerColor.BLACK
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(reply.turn.game_id, OWNER)
    assert state.engine.skill_level == MAX_SKILL_LEVEL


async def test_rematch_in_a_new_session_still_inherits_the_level(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    await _finished_game(conversation, "новая игра белыми уровень восемь", 1)

    reply = await conversation.handle(OWNER, "еще одну партию", context(4, new=True), ConversationState())

    assert reply.turn is not None
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(reply.turn.game_id, OWNER)
    assert state.engine.skill_level == 10 - 2
    assert state.player_color is PlayerColor.WHITE


async def test_rematch_during_an_active_game_is_confirmed_first(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра белыми уровень шесть", context(1))

    asked = await conversation.handle(OWNER, "реванш сложнее", context(2), started.state)
    confirmed = await conversation.handle(OWNER, "да", context(3), asked.state)

    assert asked.turn is None
    assert asked.state.game_id == started.state.game_id
    assert confirmed.turn is not None
    assert confirmed.turn.game_id != started.state.game_id
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(confirmed.turn.game_id, OWNER)
    assert state.engine.skill_level == 8


async def test_naming_a_colour_before_the_first_move_deals_again_at_once(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра белыми уровень шесть", context(1))

    switched = await conversation.handle(OWNER, "я играю чёрными", context(2), started.state)

    assert switched.turn is not None
    assert switched.state.pending_action is None
    assert switched.speech.text.startswith("Хорошо, вы играете черными.")
    assert switched.turn.game_id != started.state.game_id
    assert switched.turn.player_color is PlayerColor.BLACK
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(switched.turn.game_id, OWNER)
    # The level the player set survives the re-deal; only the side changes.
    assert state.engine.skill_level == 6


async def test_naming_a_colour_after_a_move_says_the_game_has_to_end_first(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра белыми", context(1))
    played = await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)

    asked = await conversation.handle(OWNER, "давай чёрными", context(3), played.state)
    declined = await conversation.handle(OWNER, "нет", context(4), asked.state)

    assert asked.turn is None
    assert "цвет меняется только в новой партии" in asked.speech.text
    assert declined.turn is None
    assert declined.state.game_id == started.state.game_id


async def test_naming_the_colour_already_played_changes_nothing(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра белыми", context(1))

    reply = await conversation.handle(OWNER, "я играю белыми", context(2), started.state)

    assert reply.turn is None
    assert reply.speech.text == "Вы и так играете белыми. Назовите ход."
    assert reply.state.game_id == started.state.game_id
    assert reply.state.pending_action is None


async def test_a_colour_named_with_no_game_at_all_starts_one(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, "давай чёрными", context(1))

    assert reply.turn is not None
    assert reply.turn.player_color is PlayerColor.BLACK
    assert "Вы играете черными" in reply.speech.text


async def test_rematch_without_any_previous_game_starts_nothing(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, "реванш", context(1))

    assert reply.turn is None
    assert reply.state.game_id is None
    with session_scope(session_factory) as session:
        assert GameRepository(session).find_latest(OWNER) is None


# Ten quiet moves: the tenth leaves the opening, which is the only thing in the
# whole sequence worth a remark.
TO_MIDDLEGAME = ("e2e4", "d2d4", "g1f3", "f1c4", "b1c3", "c1f4", "a2a3", "b2b3", "g2g3", "h2h3")


async def play_all(
    conversation: ConversationService,
    moves: tuple[str, ...],
    state: ConversationState,
) -> ConversationReply:
    reply = ConversationReply(Speech.of(""), state)
    for offset, move in enumerate(moves):
        reply = await conversation.handle(OWNER, move, context(10 + offset), reply.state)
    return reply


async def test_an_ordinary_move_is_played_without_any_comment(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    reply = await play_all(conversation, TO_MIDDLEGAME[:3], started.state)

    assert reply.speech.text == "Ваш ход: конь g1 f3. Мой ход. ладья g8 h8."


async def test_a_comment_survives_a_replayed_request_and_a_new_service(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))

    played = await play_all(conversation, TO_MIDDLEGAME, started.state)
    last = context(10 + len(TO_MIDDLEGAME) - 1)
    replayed = await subject(session_factory, offline_settings).handle(OWNER, TO_MIDDLEGAME[-1], last, played.state)

    assert "Партия перешла в миттельшпиль." in played.speech.text
    assert replayed.speech.text == played.speech.text


async def test_brief_answers_are_played_without_a_comment(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    await conversation.handle(OWNER, "отвечай кратко", context(1))
    started = await conversation.handle(OWNER, "новая игра", context(2))

    reply = await play_all(conversation, TO_MIDDLEGAME, started.state)

    assert "миттельшпиль" not in reply.speech.text


async def test_a_puzzle_is_offered_before_any_game_exists_and_leaves_none_behind(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    offered = await conversation.handle(OWNER, "дай задачу", context(1, new=True))
    left = await conversation.handle(OWNER, "выйти из задач", context(2), offered.state)

    assert "Задача, ход" in offered.speech.text
    assert offered.state.game_id is None
    assert "Выхожу из задач" in left.speech.text
    with session_scope(session_factory) as session:
        assert GameRepository(session).find_latest(OWNER) is None


async def test_a_puzzle_card_is_drawn_from_the_solver_side_and_the_stored_orientation(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """The picture follows the puzzle's own position; no game row is involved."""
    conversation = subject(session_factory, offline_settings)

    offered = await conversation.handle(OWNER, "дай задачу", context(1, new=True))

    open_puzzle = PuzzleService(session_factory).find_open(OWNER)
    assert open_puzzle is not None
    board = open_puzzle.board()
    # A Lichess puzzle starts after the setup move, so the side to move solves it.
    solver = PlayerColor.WHITE if board.turn is chess.WHITE else PlayerColor.BLACK
    assert isinstance(offered.card, BoardCard)
    assert offered.card.position_hash == position_hash(board, solver, open_puzzle.last_move)
    assert open_puzzle.last_move is not None

    # Pinned to the other side, the same position must be drawn the other way up.
    opposite = PlayerColor.BLACK if solver is PlayerColor.WHITE else PlayerColor.WHITE
    command = "показывай доску за черных" if opposite is PlayerColor.BLACK else "показывай доску за белых"
    pinned = await conversation.handle(OWNER, command, context(2), offered.state)
    shown = await conversation.handle(OWNER, "подскажи", context(3), pinned.state)

    assert isinstance(shown.card, BoardCard)
    assert shown.card.position_hash == position_hash(board, opposite, open_puzzle.last_move)
    assert shown.card.position_hash != offered.card.position_hash


# A long enough game for the opening and stage remarks to be spent and the
# commentary cooldown to be over, leaving the player one plain non-mating check.
BEFORE_A_PLAYER_CHECK = (
    "b2b3",
    "h7h6",
    "c2c3",
    "g8f6",
    "d1c2",
    "g7g5",
    "c2e4",
    "e7e6",
    "a2a4",
    "h8h7",
    "e2e3",
    "e8e7",
    "e4g6",
    "b8c6",
    "a4a5",
    "c6b8",
    "g6h6",
    "h7h6",
    "g1e2",
    "f6d5",
    "e3e4",
    "d8e8",
    "g2g3",
    "b8a6",
)


async def test_a_check_the_player_gave_is_never_announced_twice(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        game = repository.create_game(OWNER, PlayerColor.WHITE)
        game = repository.append_moves(game.id, OWNER, game.revision, BEFORE_A_PLAYER_CHECK)
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(
        OWNER,
        "слон цэ один а три",
        context(1),
        ConversationState(game.id, game.revision),
    )

    assert reply.speech.text.startswith("Ваш ход: слон c1 a3. Шах.")
    assert reply.speech.text.lower().count("шах") == 1


async def test_cancel_alone_takes_back_the_last_full_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    played = await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)

    cancelled = await conversation.handle(OWNER, "отмена", context(3), played.state)

    assert cancelled.speech.text == "Один полный ход отменен. Ваш ход."
    with session_scope(session_factory) as session:
        stored = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert stored.moves == ()


async def test_cancel_answers_an_open_question_instead_of_the_board(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))
    played = await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)
    asked = await conversation.handle(OWNER, "ход конем", context(3), played.state)

    cancelled = await conversation.handle(OWNER, "отмена", context(4), asked.state)

    assert asked.state.clarification is not None
    assert cancelled.speech.text == "Хорошо, ход не делаю. Назовите другой ход."
    with session_scope(session_factory) as session:
        stored = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert len(stored.moves) == 2


async def test_cancel_before_any_move_changes_nothing(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра", context(1))

    cancelled = await conversation.handle(OWNER, "отмена", context(2), started.state)

    assert cancelled.speech.text == "Хорошо, ничего не меняю. Назовите команду или попросите помощь."
    with session_scope(session_factory) as session:
        stored = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert stored.moves == ()


async def test_a_colour_named_after_the_session_forgot_the_game_asks_first(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра белыми", context(1))
    await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)

    # A new session knows no game; the one on the server is still the player's.
    asked = await conversation.handle(OWNER, "давай чёрными", context(3))

    assert asked.turn is None
    assert "цвет меняется только в новой партии" in asked.speech.text
    with session_scope(session_factory) as session:
        stored = GameRepository(session).load(started.state.game_id or "", OWNER)
    assert stored.status is GameStatus.ACTIVE


async def test_the_level_changes_mid_game_without_touching_the_position(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))
    moved = await conversation.handle(OWNER, "пешка е два е четыре", context(2), started.state)

    changed = await conversation.handle(OWNER, "уровень двенадцать", context(3), moved.state)

    assert changed.speech.text == "Установил уровень 12. Партия продолжается. Ваш ход."
    assert changed.state.game_id == moved.state.game_id
    with session_scope(session_factory) as session:
        state = GameRepository(session).load(moved.state.game_id or "", OWNER)
    assert state.engine.skill_level == 12
    assert state.moves == moved.turn.moves if moved.turn else False
    assert changed.state.revision == state.revision


async def test_asking_about_the_level_never_changes_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))

    scale = await conversation.handle(OWNER, "пятнадцатый уровень это высокий или низкий", context(2), started.state)
    capability = await conversation.handle(OWNER, "а уровень 5 как сделать", context(3), scale.state)

    assert scale.speech.text == (
        "Чем больше число, тем сильнее я играю. Ноль — самый легкий уровень, двадцать — самый сильный. "
        "Чтобы поставить, скажите: «уровень пять»."
    )
    assert capability.speech.text == (
        "Да. Назовите уровень от нуля до двадцати, например: «уровень пять». Партия продолжится с той же позиции."
    )
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(started.state.game_id or "", OWNER).engine.skill_level == 7


@pytest.mark.parametrize(
    "utterance",
    [
        "что значит пятый уровень",
        "почему у меня пятый уровень",
        "пятый уровень это сложно",
        "на пятом уровне я сильный",
    ],
)
async def test_a_question_that_names_a_level_explains_it_instead_of_setting_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))

    asked = await conversation.handle(OWNER, utterance, context(2), started.state)

    assert asked.speech.text == (
        "Чем больше число, тем сильнее я играю. Ноль — самый легкий уровень, двадцать — самый сильный. "
        "Чтобы поставить, скажите: «уровень пять»."
    )
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(started.state.game_id or "", OWNER).engine.skill_level == 7


@pytest.mark.parametrize(
    "utterance",
    ["поставь уровень на пять", "изменить уровень на пять", "можно уровень пять", "уровень пять пожалуйста"],
)
async def test_a_polite_or_prepositional_level_command_is_still_a_command(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
    utterance: str,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))

    changed = await conversation.handle(OWNER, utterance, context(2), started.state)

    assert changed.speech.text == "Установил уровень 5. Партия продолжается. Ваш ход."
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(started.state.game_id or "", OWNER).engine.skill_level == 5


async def test_naming_the_same_level_again_asks_for_a_different_one(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))

    reply = await conversation.handle(OWNER, "уровень семь", context(2), started.state)

    assert reply.speech.text == "Уровень 7 уже установлен. Назовите другой уровень — от нуля до двадцати."


async def test_the_level_waits_while_the_engine_still_owes_a_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        state = repository.load(started.state.game_id or "", OWNER)
        repository.append_moves(state.id, OWNER, state.revision, ("e2e4",))

    reply = await conversation.handle(OWNER, "уровень двенадцать", context(2), started.state)

    assert reply.speech.text == "Сначала я сделаю свой ход. Скажите «продолжаем», потом повторите уровень."


async def test_the_level_command_starts_a_game_when_there_is_none(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    reply = await subject(session_factory, offline_settings).handle(OWNER, "нулевой уровень", context(1))

    assert "Новая партия. Вы играете белыми, уровень 0." in reply.speech.text


async def test_an_open_puzzle_owns_the_turn_before_a_level_command_does(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))
    offered = await conversation.handle(OWNER, "дай задачу", context(2), started.state)

    reply = await conversation.handle(OWNER, "уровень двенадцать", context(3), offered.state)

    assert reply.speech.text == "Сейчас открыта задача. Скажите «вернуться к партии», потом назовите уровень."
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(started.state.game_id or "", OWNER).engine.skill_level == 7


async def test_a_redelivered_level_command_repeats_its_answer_once(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))

    first = await conversation.handle(OWNER, "уровень двенадцать", context(2), started.state)
    again = await conversation.handle(OWNER, "уровень двенадцать", context(2), started.state)

    assert again.speech.text == first.speech.text
    assert again.state.revision == first.state.revision
    with session_scope(session_factory) as session:
        assert GameRepository(session).load(started.state.game_id or "", OWNER).revision == first.state.revision


async def test_a_redelivered_level_command_leaves_the_same_question_answered(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """A retry must not resurrect the confirmation the original request cancelled."""
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))
    asked = await conversation.handle(OWNER, "новая игра", context(2), started.state)

    first = await conversation.handle(OWNER, "уровень двенадцать", context(3), asked.state)
    again = await conversation.handle(OWNER, "уровень двенадцать", context(3), asked.state)

    assert asked.state.pending_action is not None
    assert first.state.pending_action is None
    assert again.state == first.state


async def test_a_redelivered_level_command_closes_help_and_paging_the_same_way(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "новая игра уровень семь", context(1))
    read = await conversation.handle(OWNER, "какая позиция", context(2), started.state)
    opened = await conversation.handle(OWNER, "что ты умеешь", context(3), read.state)

    first = await conversation.handle(OWNER, "уровень двенадцать", context(4), opened.state)
    again = await conversation.handle(OWNER, "уровень двенадцать", context(4), opened.state)

    assert opened.state.help is not None
    assert first.state.help is None
    assert first.state.position_page == 0
    assert again.state == first.state
