"""End-to-end voice conversation tests without the Alice transport."""

from __future__ import annotations

import chess
import pytest
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import CommandKind, PendingClarification, route
from yura_chess.application.conversation import (
    MAX_SKILL_LEVEL,
    ConversationReply,
    ConversationService,
    ConversationState,
)
from yura_chess.application.game_service import RequestContext
from yura_chess.application.puzzle_service import PuzzleService
from yura_chess.domain.analysis import MoveCandidate, PositionAnalysis, Score
from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.domain.preferences import BoardOrientation, DetailLevel, NotationStyle
from yura_chess.presentation.board_image import position_hash
from yura_chess.presentation.help_speech import SECTIONS, HelpState, HelpTopic
from yura_chess.presentation.move_speech import PAUSE_MARKUP, Speech
from yura_chess.presentation.response_composer import BoardCard
from yura_chess.settings import Settings
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.review_repository import ReviewRepository

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
        ("справка про факты", HelpTopic.FACTS, "За кого я играю"),
        ("справка про партию", HelpTopic.GAME, "уровень десять"),
        ("справка про настройки", HelpTopic.SETTINGS, "Говори кратко"),
        ("справка про тренера", HelpTopic.TRAINING, "режим тренера"),
        ("справка про разбор", HelpTopic.REVIEW, "Разбери партию"),
        ("справка про задачи", HelpTopic.PUZZLES, "Дай задачу"),
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
    "почему ты так сходила",
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
    "сколько я ошибся",
    "продиктуй ходы",
    "покажи pgn",
    "сыграть эту позицию заново",
    "выйти из разбора",
    # Puzzles.
    "дай задачу",
    "следующая задача",
    "покажи решение",
    "какая у меня серия",
    "вернуться к партии",
    # Speech.
    "что ты услышала",
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
    while "Скажите «дальше»" in reply.speech.text:
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


async def test_help_inside_a_puzzle_names_the_puzzle_and_leaves_the_attempt_alone(
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

    assert "Идет задача" in helped.speech.text
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
    await conversation.handle(OWNER, "короткая нотация", context(2), started.state)

    reply = await conversation.handle(OWNER, "пешка е два е четыре", context(3), started.state)

    assert reply.turn is not None
    engine_move = reply.turn.engine_move or ""
    assert f" {engine_move[2:4]}." in reply.speech.text
    assert engine_move[:2] not in reply.speech.text


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

    assert "уровень сложности" in brief.speech.text
    assert "новая игра уровень десять" not in brief.speech.text
    assert "новая игра уровень десять" in detailed.speech.text
    assert detailed_move.speech.text.endswith("Сейчас ваш ход.")


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

    assert reply.speech.text == "Ваш ход: g1f3. Мой ход. ладья g8 h8."


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
