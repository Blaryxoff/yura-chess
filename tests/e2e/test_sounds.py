"""Every non-verbal cue over both halves of the suite: the shell script and real Alice JSON.

`add_sound` is unit-tested on its own. Only a whole dialogue can prove that the
five events are actually reachable, that an answer never carries a second cue,
that a re-delivery repeats the first one, and that the durable switch outlives
the Alice session that flipped it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import chess
import pytest
from harness import AliceSession, FakeEngine, build_client, context
from sqlalchemy.orm import Session, sessionmaker
from test_modes import conversation, run_script

from yura_chess.adapters.alice.models import TTS_LIMIT
from yura_chess.application.conversation import ConversationReply, ConversationState
from yura_chess.presentation.move_speech import PAUSE_MARKUP
from yura_chess.settings import Settings

pytestmark = pytest.mark.anyio

SPEAKER = re.compile(r'<speaker audio="([^"]+)">')


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


def cue(tts: str | None) -> str | None:
    """The one audio id an answer carries, and proof that it carries no second one."""
    found = SPEAKER.findall(tts or "")
    assert len(found) <= 1, tts
    return found[0] if found else None


async def play(
    session_factory: sessionmaker[Session],
    settings: Settings,
    replies: Sequence[str],
    commands: Sequence[str],
    owner: str,
) -> list[str | None]:
    """Open a game and speak the line; keep the cue of every answer in order."""
    service = conversation(session_factory, settings, ScriptedEngine(replies))
    opening = await service.handle(owner, "", context(owner, 0, new=True))
    heard = [cue(opening.speech.tts)]
    state = opening.state
    for step, command in enumerate(commands, start=1):
        reply = await service.handle(owner, command, context(owner, step), state)
        heard.append(cue(reply.speech.tts))
        state = reply.state
    return heard


async def alice_cues(
    session_factory: sessionmaker[Session],
    replies: Sequence[str],
    commands: Sequence[str],
    session_id: str,
) -> list[str | None]:
    """The same line over real Alice JSON, so the adapter is proven to carry it."""
    async with build_client(session_factory, ScriptedEngine(replies)) as client:
        dialogue = AliceSession(client, session_id)
        answers = [await dialogue.say(new=True)]
        for command in commands:
            answers.append(await dialogue.say(command))
    return [cue(body["response"].get("tts")) for body in answers]


def heard_after(transcript: list[tuple[str, ConversationReply]], command: str) -> str | None:
    for utterance, reply in transcript:
        if utterance == command:
            return cue(reply.speech.tts)
    raise AssertionError(f"{command!r} is not in the script")


# The engine replies, the player's commands and the cue every answer must carry.
LINES = (
    # 1. f3 e5 2. a3 Qh4+ — the engine, not the player, delivers the check.
    ("check", ("e7e5", "d8h4"), ("пешка эф два эф три", "пешка а два а три"), ("start", "move", "check")),
    # 1. f3 e5 2. g4 Qh4# — mate outranks the check it also is.
    ("mate", ("e7e5", "d8h4"), ("пешка эф два эф три", "пешка ж два ж четыре"), ("start", "move", "checkmate")),
    # 1. e4 f6 2. d4 g5 3. Qh5#
    (
        "win",
        ("f7f6", "g7g5"),
        ("пешка е два е четыре", "пешка д два д четыре", "ферзь д один аш пять"),
        ("start", "move", "move", "success"),
    ),
    # The engine has already moved for Black, and «ход» outranks «начало».
    ("black", ("e2e4",), ("новая игра черными уровень три", "да"), ("start", None, "move")),
)


@pytest.mark.parametrize("driver", ["shell", "alice"])
@pytest.mark.parametrize(("label", "replies", "commands", "expected"), LINES)
async def test_a_scripted_line_is_heard_cue_by_cue_over_both_transports(
    driver: str,
    label: str,
    replies: tuple[str, ...],
    commands: tuple[str, ...],
    expected: tuple[str, ...],
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    name = f"e2e-sound-{label}-{driver}"
    heard = await (
        play(session_factory, offline_settings, replies, commands, name)
        if driver == "shell"
        else alice_cues(session_factory, replies, commands, name)
    )

    assert heard == [getattr(offline_settings, f"alice_sound_{event}") if event else None for event in expected]


async def test_the_shell_script_switches_the_cues_off_and_back_on(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """The two orientation Validation Commands feed this script to the runner."""
    transcript = await run_script(session_factory, offline_settings, FakeEngine(), owner="e2e-sound-script")

    assert cue(transcript[0][1].speech.tts) == offline_settings.alice_sound_start
    assert heard_after(transcript, "выключи звуки") is None
    assert heard_after(transcript, "пешка е два е четыре") is None
    assert heard_after(transcript, "конь ж один эф три") is None
    assert heard_after(transcript, "включи звуки") is None
    assert heard_after(transcript, "дай задачу") == offline_settings.alice_sound_start


async def test_alice_repeats_the_cue_of_a_redelivered_move(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    async with build_client(session_factory) as client:
        dialogue = AliceSession(client, "e2e-sound-replay")
        opened = await dialogue.say(new=True)
        moved = await dialogue.say("пешка е два е четыре")
        retry = await dialogue.resend(dialogue.message_id, "пешка е два е четыре")

    assert opened["response"]["tts"].count("<speaker") == 1
    assert offline_settings.alice_sound_start in opened["response"]["tts"]
    assert moved["response"]["tts"].count("<speaker") == 1
    assert offline_settings.alice_sound_move in moved["response"]["tts"]
    assert retry["response"]["tts"] == moved["response"]["tts"]


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
    assert offline_settings.alice_sound_move in loud["response"]["tts"]


async def test_a_cue_on_top_of_extended_pauses_still_fits_the_platform_limit(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """Measured before the adapter clips: a clipped answer loses its last words."""
    service = conversation(session_factory, offline_settings, FakeEngine())
    state = ConversationState()
    owner = "e2e-sound-limit"
    spoken = []
    commands = ("говори медленнее", "говори подробнее", "новая игра белыми уровень три", "да")
    for step, command in enumerate(("", *commands), start=1):
        reply = await service.handle(owner, command, context(owner, step, new=step == 1), state)
        state = reply.state
        spoken.append(reply.speech.spoken())

    assert [answer for answer in spoken if "<speaker" in answer and PAUSE_MARKUP in answer]
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
    assert cue(moved.speech.tts) == offline_settings.alice_sound_move
    assert "Ваш ход" in first.speech.text
    assert "<speaker" not in first.speech.text
    assert first.speech.text == second.speech.text
    assert cue(first.speech.tts) is None
    assert cue(second.speech.tts) is None
