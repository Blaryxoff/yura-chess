"""Every non-verbal cue over both halves of the suite: the shell script and real Alice JSON.

`add_sound` is unit-tested on its own. Only a whole dialogue can prove that the
five events are actually reachable, that an answer narrating both halves of a
turn sounds both of them and on the right words, that a re-delivery repeats them,
and that the durable switch outlives the Alice session that flipped it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import chess
import pytest
from harness import AliceSession, FakeEngine, build_client, context
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker
from test_modes import conversation, run_script

from yura_chess.adapters.alice.models import TTS_LIMIT
from yura_chess.application.conversation import ConversationReply, ConversationState
from yura_chess.presentation.move_speech import ENGINE_MOVE_PREFIX, PAUSE_MARKUP
from yura_chess.settings import Settings

pytestmark = pytest.mark.anyio

SPEAKER = re.compile(r'<speaker audio="([^"]+)">')
BEFORE_ENGINE = re.compile(r'<speaker audio="([^"]+)">\s*$')


class ScriptedEngine(FakeEngine):
    """Plays a forced line, so a real check and a real mate are reachable."""

    def __init__(self, replies: Sequence[str]) -> None:
        super().__init__()
        self._replies = list(replies)

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        self.searches += 1
        if self._replies:
            return self._replies.pop(0)
        return next(iter(board.legal_moves)).uci()


def cues(tts: str | None) -> tuple[str, ...]:
    """Every audio id an answer carries, in the order it is heard."""
    return tuple(SPEAKER.findall(tts or ""))


def engine_cue(tts: str | None) -> str | None:
    """The id sounded where the engine's half of the answer begins, if any."""
    spoken = tts or ""
    start = spoken.find(ENGINE_MOVE_PREFIX)
    if start < 0:
        return None
    tagged = BEFORE_ENGINE.search(spoken[:start])
    return tagged.group(1) if tagged is not None else None


async def play(
    session_factory: sessionmaker[Session],
    settings: Settings,
    replies: Sequence[str],
    commands: Sequence[str],
    owner: str,
) -> list[tuple[str, ...]]:
    """Open a game and speak the line; keep the cues of every answer in order."""
    service = conversation(session_factory, settings, ScriptedEngine(replies))
    opening = await service.handle(owner, "", context(owner, 0, new=True))
    heard = [cues(opening.speech.tts)]
    state = opening.state
    for step, command in enumerate(commands, start=1):
        reply = await service.handle(owner, command, context(owner, step), state)
        heard.append(cues(reply.speech.tts))
        state = reply.state
    return heard


async def alice_cues(
    session_factory: sessionmaker[Session],
    replies: Sequence[str],
    commands: Sequence[str],
    session_id: str,
) -> list[tuple[str, ...]]:
    """The same line over real Alice JSON, so the adapter is proven to carry it."""
    async with build_client(session_factory, ScriptedEngine(replies)) as client:
        dialogue = AliceSession(client, session_id)
        answers = [await dialogue.say(new=True)]
        for command in commands:
            answers.append(await dialogue.say(command))
    return [cues(body["response"].get("tts")) for body in answers]


def heard_after(transcript: list[tuple[str, ConversationReply]], command: str) -> tuple[str, ...]:
    for utterance, reply in transcript:
        if utterance == command:
            return cues(reply.speech.tts)
    raise AssertionError(f"{command!r} is not in the script")


# The engine replies, the player's commands and the cues every answer must carry:
# an answer that narrates both halves of a turn sounds both of them.
LINES = (
    # 1. f3 e5 2. a3 Qh4+ — the engine, not the player, delivers the check.
    (
        "check",
        ("e7e5", "d8h4"),
        ("пешка эф два эф три", "пешка а два а три"),
        (("start",), ("move", "move"), ("move", "check")),
    ),
    # 1. e4 f6 2. Qh5+ g6 — this time the check is the player's, and the engine
    # parries it, so only the first half of the answer may sound alarmed.
    (
        "player-check",
        ("f7f6", "g7g6"),
        ("пешка е два е четыре", "ферзь д один аш пять"),
        (("start",), ("move", "move"), ("check", "move")),
    ),
    # 1. f3 e5 2. g4 Qh4# — mate outranks the check it also is.
    (
        "mate",
        ("e7e5", "d8h4"),
        ("пешка эф два эф три", "пешка ж два ж четыре"),
        (("start",), ("move", "move"), ("move", "checkmate")),
    ),
    # 1. e4 f6 2. d4 g5 3. Qh5# — the last answer has no engine half to sound.
    (
        "win",
        ("f7f6", "g7g5"),
        ("пешка е два е четыре", "пешка д два д четыре", "ферзь д один аш пять"),
        (("start",), ("move", "move"), ("move", "move"), ("success",)),
    ),
    # The engine has already moved for Black, and «ход» outranks «начало».
    ("black", ("e2e4",), ("новая игра черными уровень три", "да"), (("start",), (), ("move",))),
)


@pytest.mark.parametrize("driver", ["shell", "alice"])
@pytest.mark.parametrize(("label", "replies", "commands", "expected"), LINES)
async def test_a_scripted_line_is_heard_cue_by_cue_over_both_transports(
    driver: str,
    label: str,
    replies: tuple[str, ...],
    commands: tuple[str, ...],
    expected: tuple[tuple[str, ...], ...],
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    name = f"e2e-sound-{label}-{driver}"
    heard = await (
        play(session_factory, offline_settings, replies, commands, name)
        if driver == "shell"
        else alice_cues(session_factory, replies, commands, name)
    )

    assert heard == [
        tuple(getattr(offline_settings, f"alice_sound_{event}") for event in answer) for answer in expected
    ]


async def test_the_shell_script_switches_the_cues_off_and_back_on(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """The two orientation Validation Commands feed this script to the runner."""
    transcript = await run_script(session_factory, offline_settings, FakeEngine(), owner="e2e-sound-script")

    assert cues(transcript[0][1].speech.tts) == (offline_settings.alice_sound_start,)
    assert heard_after(transcript, "выключи звуки") == ()
    assert heard_after(transcript, "пешка е два е четыре") == ()
    assert heard_after(transcript, "конь ж один эф три") == ()
    assert heard_after(transcript, "включи звуки") == ()
    assert heard_after(transcript, "дай задачу") == (offline_settings.alice_sound_start,)


async def test_alice_repeats_the_cue_of_a_redelivered_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    async with build_client(session_factory) as client:
        dialogue = AliceSession(client, "e2e-sound-replay")
        opened = await dialogue.say(new=True)
        moved = await dialogue.say("пешка е два е четыре")
        retry = await dialogue.resend(dialogue.message_id, "пешка е два е четыре")

    assert cues(opened["response"]["tts"]) == (offline_settings.alice_sound_start,)
    assert cues(moved["response"]["tts"]) == (offline_settings.alice_sound_move,) * 2
    # The second of the two belongs to the engine's half and must sit on its words.
    assert engine_cue(moved["response"]["tts"]) == offline_settings.alice_sound_move
    assert retry["response"]["tts"] == moved["response"]["tts"]


async def test_an_owed_reply_sounds_the_move_it_owes_and_never_the_one_already_heard(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """«продолжаем» says only «Мой ход», so only the engine's half may sound."""
    async with build_client(session_factory, FakeEngine(move_failures=1)) as client:
        dialogue = AliceSession(client, "e2e-sound-owed")
        await dialogue.say(new=True)
        stalled = await dialogue.say("пешка е два е четыре")
        recovered = await dialogue.say("продолжаем")

    assert cues(stalled["response"].get("tts")) == (offline_settings.alice_sound_move,)
    assert recovered["response"]["text"].startswith(ENGINE_MOVE_PREFIX)
    assert cues(recovered["response"]["tts"]) == (offline_settings.alice_sound_move,)
    assert engine_cue(recovered["response"]["tts"]) == offline_settings.alice_sound_move


async def test_a_resumed_game_settles_its_owed_reply_without_re_sounding_the_old_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """Naming a move for the side the engine owes settles that reply instead.

    The player's own move of a session ago must not be said, nor sounded, twice.
    """
    async with build_client(session_factory, FakeEngine(move_failures=1)) as client:
        opening = AliceSession(client, "e2e-sound-owed-resume-1")
        await opening.say(new=True)
        stalled = await opening.say("пешка е два е четыре")

        resumed = AliceSession(client, "e2e-sound-owed-resume-2")
        await resumed.say(new=True)
        settled = await resumed.say("конь ж восемь аш шесть")

    assert "Ваш ход: пешка e2 e4" in stalled["response"]["text"]
    assert settled["response"]["text"] == "Мой ход. конь g8 h6."
    assert cues(settled["response"]["tts"]) == (offline_settings.alice_sound_move,)
    assert engine_cue(settled["response"]["tts"]) == offline_settings.alice_sound_move


async def test_a_re_delivered_owed_reply_repeats_the_answer_it_first_gave(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
    offline_settings: Settings,
) -> None:
    """Without its cached Alice answer the reply is rebuilt, and must come out identical."""
    async with build_client(session_factory, FakeEngine(move_failures=1)) as client:
        dialogue = AliceSession(client, "e2e-sound-owed-replay")
        await dialogue.say(new=True)
        await dialogue.say("пешка е два е четыре")
        recovered = await dialogue.say("продолжаем")
        settled_id = dialogue.message_id
        # The game moves on, so a late re-delivery has a newer history to be
        # misled by: its answer still belongs to the turn it settled.
        await dialogue.say("пешка д два д четыре")
        with database_engine.begin() as connection:
            connection.execute(text("UPDATE request_replays SET alice_response_payload = NULL"))

        rebuilt = await dialogue.resend(settled_id, "продолжаем")

    assert recovered["response"]["text"].startswith(ENGINE_MOVE_PREFIX)
    assert rebuilt["response"]["text"] == recovered["response"]["text"]
    assert cues(rebuilt["response"]["tts"]) == cues(recovered["response"]["tts"])
    assert cues(rebuilt["response"]["tts"]) == (offline_settings.alice_sound_move,)


async def test_the_sound_switch_outlives_the_alice_session_that_flipped_it(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    async with build_client(session_factory) as client:
        first = AliceSession(client, "e2e-sound-switch-1")
        await first.say(new=True)
        off = await first.say("выключи звуки")
        silent = await first.say("пешка е два е четыре")

        second = AliceSession(client, "e2e-sound-switch-2")
        resumed = await second.say(new=True)
        on = await second.say("включи звуки")
        loud = await second.say("конь ж один эф три")

    assert "Звуки выключены." in off["response"]["text"]
    assert "<speaker" not in (silent["response"].get("tts") or "")
    assert "<speaker" not in (resumed["response"].get("tts") or "")
    assert "Звуки включены." in on["response"]["text"]
    assert cues(loud["response"]["tts"]) == (offline_settings.alice_sound_move,) * 2


async def test_both_cues_on_top_of_extended_pauses_still_fit_the_platform_limit(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """Measured before the adapter clips: a clipped answer loses its last words."""
    service = conversation(session_factory, offline_settings, FakeEngine())
    state = ConversationState()
    owner = "e2e-sound-limit"
    spoken = []
    commands = (
        "говори медленнее",
        "говори подробнее",
        "новая игра белыми уровень три",
        "да",
        "пешка е два е четыре",
    )
    for step, command in enumerate(("", *commands), start=1):
        reply = await service.handle(owner, command, context(owner, step, new=step == 1), state)
        state = reply.state
        spoken.append(reply.speech.spoken())

    assert [answer for answer in spoken if answer.count("<speaker") == 2 and PAUSE_MARKUP in answer]
    for answer in spoken:
        assert len(answer) <= TTS_LIMIT


async def test_a_repeat_says_the_words_again_without_the_cue_that_came_with_them(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    service = conversation(session_factory, offline_settings, FakeEngine())
    state = ConversationState()
    owner = "e2e-sound-repeat"
    answers = []
    for step, command in enumerate(("", "пешка е два е четыре", "повтори", "повтори"), start=1):
        reply = await service.handle(owner, command, context(owner, step, new=step == 1), state)
        state = reply.state
        answers.append(reply)

    moved, first, second = answers[1], answers[2], answers[3]
    assert cues(moved.speech.tts) == (offline_settings.alice_sound_move,) * 2
    assert "Ваш ход" in first.speech.text
    assert "<speaker" not in first.speech.text
    assert first.speech.text == second.speech.text
    assert cues(first.speech.tts) == ()
    assert cues(second.speech.tts) == ()
