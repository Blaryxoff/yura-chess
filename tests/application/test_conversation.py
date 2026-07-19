"""End-to-end voice conversation tests without the Alice transport."""

from __future__ import annotations

import chess
import pytest
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import CommandKind, PendingClarification
from yura_chess.application.conversation import ConversationService, ConversationState
from yura_chess.application.game_service import RequestContext
from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.presentation.help_speech import HelpState, HelpTopic
from yura_chess.settings import Settings
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository

pytestmark = pytest.mark.anyio

OWNER = "c" * 64


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
    assert "Ваш ход: e2e4" in reply.speech.text
    assert "Мой ход" in reply.speech.text


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


async def test_position_and_repeat_heard_are_available_without_alice(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    position = await conversation.handle(OWNER, "что на е два", context(2), started.state)
    repeated = await conversation.handle(OWNER, "что ты услышала", context(3), position.state)

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
    heard = await conversation.handle(OWNER, "что ты услышала", context(4), repeated.state)

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
        "Сейчас установлен уровень сложности 7 из 20. Чтобы изменить его, скажите «новая игра уровень десять»."
    )


async def test_new_session_offers_the_latest_unfinished_game_and_last_two_moves(
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
    assert "Последние два хода" in prompt.speech.text
    assert "пешка e2 e4" in prompt.speech.text
    assert "пешка e7 e5" in prompt.speech.text

    resumed = await conversation.handle(OWNER, "да", context(2), prompt.state)

    assert resumed.turn is not None
    assert resumed.turn.game_id == game.id


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
        "новая игра черными уровень двенадцать",
        context(2),
        started.state,
    )
    confirmed = await conversation.handle(OWNER, "да", context(3), asked.state)

    assert asked.turn is None
    assert confirmed.turn is not None
    assert confirmed.turn.game_id != started.state.game_id
    assert confirmed.turn.player_color is PlayerColor.BLACK
    with session_scope(session_factory) as session:
        game = GameRepository(session).load(confirmed.turn.game_id, OWNER)
    assert game.engine.skill_level == 12


async def test_help_before_a_game_offers_topics_without_starting_anything(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)

    reply = await conversation.handle(OWNER, "справка", context(1))

    assert "Партия еще не начата" in reply.speech.text
    assert "Разделы справки" in reply.speech.text
    assert reply.state.help == HelpState(topic=None, page=0)
    assert reply.state.game_id is None
    assert reply.turn is None


@pytest.mark.parametrize(
    ("utterance", "expected", "phrase"),
    [
        ("справка по ходам", HelpTopic.MOVES, "«пешка е два е четыре»"),
        ("справка по позиции", HelpTopic.POSITION, "две горизонтали"),
        ("справка про партию", HelpTopic.GAME, "уровень десять"),
        ("справка про речь", HelpTopic.SPEECH, "Что ты услышала"),
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


async def test_help_navigation_walks_the_catalogue_forward_back_and_to_the_start(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    opened = await conversation.handle(OWNER, "все команды", context(1))
    assert "Скажите «дальше»" in opened.speech.text

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

    reply = await conversation.handle(OWNER, "справка по настройкам", context(1))

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

    assert "Идет партия" in helped.speech.text
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


async def test_help_after_a_finished_game_says_the_game_is_over(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    conversation = subject(session_factory, offline_settings)
    started = await conversation.handle(OWNER, "", context(1))
    asked = await conversation.handle(OWNER, "сдаюсь", context(2), started.state)
    resigned = await conversation.handle(OWNER, "да", context(3), asked.state)

    reply = await conversation.handle(OWNER, "справка", context(4), resigned.state)

    assert "Партия закончена" in reply.speech.text


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
