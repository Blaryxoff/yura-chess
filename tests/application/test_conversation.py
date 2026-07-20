"""End-to-end voice conversation tests without the Alice transport."""

from __future__ import annotations

import chess
import pytest
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import CommandKind, PendingClarification
from yura_chess.application.conversation import ConversationService, ConversationState
from yura_chess.application.game_service import RequestContext
from yura_chess.domain.game import GameStatus, PlayerColor
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
    assert "играть с вами в шахматы голосом" in reply.speech.text
    assert "новая игра белыми" in reply.speech.text
    assert "пешка е два е четыре" in reply.speech.text


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
