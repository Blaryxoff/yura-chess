"""Whole Alice dialogues over real JSON: screens, restarts, retries and isolation.

The adapter suite checks one request at a time. This module checks what a device
actually does: it carries `user_state_update` and `session_state` forward across
many turns, it re-delivers requests, it comes back in a new session, and it does
all of that next to another player on the same deployment.
"""

from __future__ import annotations

import asyncio

import chess
import pytest
from harness import USER_A, USER_B, AliceSession, FakeEngine, build_client
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.adapters.alice.models import STATE_LIMIT_BYTES, TEXT_LIMIT, TTS_LIMIT

pytestmark = pytest.mark.anyio

# Every request that may change stored state; each is re-delivered in the replay test.
MUTATIONS = (
    "пешка е два е четыре",
    "включи тренера",
    "дай подсказку",
    "конь ж один эф три",
    "говори кратко",
    "дай задачу",
    "выйти из задач",
)


def games_count(database_engine: Engine) -> int:
    with database_engine.begin() as connection:
        return int(connection.execute(text("SELECT COUNT(*) FROM games")).scalar_one())


def stored_moves(database_engine: Engine, game_id: str) -> list[str]:
    with database_engine.begin() as connection:
        rows = connection.execute(
            text("SELECT uci FROM game_moves WHERE game_id = :game ORDER BY ply"),
            {"game": game_id},
        ).all()
    return [row[0] for row in rows]


async def test_a_full_dialogue_holds_together_with_a_screen(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        dialogue = AliceSession(client, "e2e-screen", screen=True)
        opened = await dialogue.say(new=True)
        answers = [opened]
        for command in ("что ты умеешь", "дальше", "выйти из справки", *MUTATIONS):
            answers.append(await dialogue.say(command))

    for body in answers:
        assert body["version"] == "1.0"
        assert body["response"]["text"]
        assert len(body["response"]["text"]) <= TEXT_LIMIT
        tts = body["response"].get("tts")
        assert tts is None or len(tts) <= TTS_LIMIT
        assert len(str(body.get("session_state", {})).encode("utf-8")) <= STATE_LIMIT_BYTES


async def test_the_same_dialogue_says_everything_without_a_screen(
    session_factory: sessionmaker[Session],
) -> None:
    """A voice-only device must lose the picture and nothing else."""
    commands = ("что ты умеешь", "пешка е два е четыре", "какая позиция")
    async with build_client(session_factory) as client:
        with_screen = AliceSession(client, "e2e-with-screen", screen=True)
        without = AliceSession(client, "e2e-without-screen", user_id=USER_B, screen=False)
        await with_screen.say(new=True)
        await without.say(new=True)
        seen = [await with_screen.say(command) for command in commands]
        heard = [await without.say(command) for command in commands]

    assert [body["response"]["text"] for body in seen] == [body["response"]["text"] for body in heard]
    assert all(body["response"].get("card") is None for body in heard)


async def test_a_restarted_session_resumes_the_same_game(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        first = AliceSession(client, "e2e-restart-1")
        await first.say(new=True)
        moved = await first.say("пешка е два е четыре")
        game_id = moved["user_state_update"]["game_id"]

        # A new session starts with empty state, exactly as a device would.
        second = AliceSession(client, "e2e-restart-2")
        prompted = await second.say(new=True)
        resumed = await second.say("да")

    assert "Продолжить?" in prompted["response"]["text"]
    assert resumed["user_state_update"]["game_id"] == game_id
    assert games_count(database_engine) == 1


async def test_every_mutation_request_is_safe_to_redeliver(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    """Alice retries a delivery; a retry must answer identically and change nothing."""
    async with build_client(session_factory) as client:
        dialogue = AliceSession(client, "e2e-replay")
        await dialogue.say(new=True)
        first_answers = []
        message_ids = []
        for command in MUTATIONS:
            first_answers.append(await dialogue.say(command))
            message_ids.append(dialogue.message_id)
        game_id = dialogue.user_state["game_id"]
        after_first = stored_moves(database_engine, game_id)

        retries = [
            await dialogue.resend(message_id, command)
            for message_id, command in zip(message_ids, MUTATIONS, strict=True)
        ]

    for first, retry in zip(first_answers, retries, strict=True):
        assert retry["response"]["text"] == first["response"]["text"]
    assert stored_moves(database_engine, game_id) == after_first
    assert games_count(database_engine) == 1


async def test_a_reused_replay_key_with_another_command_changes_nothing(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        dialogue = AliceSession(client, "e2e-fingerprint")
        await dialogue.say(new=True)
        moved = await dialogue.say("пешка е два е четыре")
        game_id = moved["user_state_update"]["game_id"]
        before = stored_moves(database_engine, game_id)

        conflicting = await dialogue.resend(dialogue.message_id, "конь ж один эф три")

    assert "Не расслышала" in conflicting["response"]["text"]
    assert conflicting.get("user_state_update") is None
    assert stored_moves(database_engine, game_id) == before


async def test_two_players_never_see_each_other_on_one_deployment(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        one = AliceSession(client, "e2e-isolation-a", user_id=USER_A)
        other = AliceSession(client, "e2e-isolation-b", user_id=USER_B)
        await one.say(new=True)
        await other.say(new=True)
        await one.say("пешка е два е четыре")
        await other.say("пешка д два д четыре")
        one_game = one.user_state["game_id"]
        other_game = other.user_state["game_id"]

        # The second player claims the first player's game id.
        other.user_state = dict(other.user_state, game_id=one_game)
        intruded = await other.say("какая позиция")

    assert one_game != other_game
    assert games_count(database_engine) == 2
    assert "e2e4" not in str(intruded)
    assert stored_moves(database_engine, one_game)[0] == "e2e4"
    assert "e2e4" not in stored_moves(database_engine, other_game)


async def test_a_pending_engine_turn_is_finished_by_the_next_request(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    """The player's move is stored even when the engine cannot answer for it."""
    async with build_client(session_factory, FakeEngine(move_failures=1)) as client:
        dialogue = AliceSession(client, "e2e-pending")
        await dialogue.say(new=True)
        stalled = await dialogue.say("пешка е два е четыре")
        game_id = dialogue.user_state["game_id"]
        stalled_moves = stored_moves(database_engine, game_id)

        recovered = await dialogue.say("продолжить")

    assert stalled["response"]["text"]
    assert stalled_moves == ["e2e4"]
    assert recovered["response"]["text"]
    assert len(stored_moves(database_engine, game_id)) == 2


async def test_a_slow_engine_answers_inside_the_platform_deadline(
    session_factory: sessionmaker[Session],
) -> None:
    engine = FakeEngine(move_delay=0.5)
    async with build_client(session_factory, engine, deadline=0.2) as client:
        dialogue = AliceSession(client, "e2e-deadline")
        await dialogue.say(new=True)
        timed_out = await dialogue.say("пешка е два е четыре")

        # The move survived the timeout; the retry finishes the engine's reply.
        finished = await dialogue.say("продолжаем")

    assert "чуть больше времени" in timed_out["response"]["text"]
    assert finished["response"]["text"]


async def test_parallel_dialogues_stay_consistent_under_a_busy_engine(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    """More callers than the pool can serve must not lose a single player move."""
    engine = FakeEngine(move_delay=0.05)
    async with build_client(session_factory, engine) as client:
        dialogues = [AliceSession(client, f"e2e-parallel-{index}", user_id=f"e2e-user-{index}") for index in range(6)]
        await asyncio.gather(*(dialogue.say(new=True) for dialogue in dialogues))
        await asyncio.gather(*(dialogue.say("пешка е два е четыре") for dialogue in dialogues))

    assert games_count(database_engine) == len(dialogues)
    for dialogue in dialogues:
        moves = stored_moves(database_engine, dialogue.user_state["game_id"])
        assert moves[0] == "e2e4"
        board = chess.Board()
        for move in moves:
            board.push(chess.Move.from_uci(move))
