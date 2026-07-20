"""The whole lifecycle in one dialogue: help, game, preferences, training, review, puzzles.

This is the transport-free half of the end-to-end suite. It drives the same
`ConversationService` the Alice webhook and the shell runner both drive, with the
script the two shell `Validation Commands` feed to the runner, so a mode that
only works behind Alice fails here.
"""

from __future__ import annotations

import random
from pathlib import Path

import chess
import pytest
from harness import FakeEngine, context
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.conversation import ConversationReply, ConversationService, ConversationState
from yura_chess.application.puzzle_service import PuzzleService
from yura_chess.cli import format_board
from yura_chess.domain.game import GameMode, GameStatus
from yura_chess.settings import Settings
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository

pytestmark = pytest.mark.anyio

OWNER = "e2e-owner-modes"
SCRIPT = Path(__file__).parent / "fixtures" / "full_help_and_modes.txt"
# The one answer that means the skill did not understand a scripted command.
FALLBACK = "Не поняла команду."


def script_commands() -> list[str]:
    """The fixture as the shell runner reads it: comments and blank lines dropped."""
    return [
        line.strip()
        for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def conversation(session_factory: sessionmaker[Session], settings: Settings, engine: FakeEngine) -> ConversationService:
    # A fixed source keeps the puzzle the catalogue offers the same on every run.
    return ConversationService(session_factory, engine, settings, PuzzleService(session_factory, random.Random(17)))


async def run_script(
    session_factory: sessionmaker[Session],
    settings: Settings,
    engine: FakeEngine,
    owner: str = OWNER,
) -> list[tuple[str, ConversationReply]]:
    """Replay the shell fixture and keep every utterance with the answer it got."""
    service = conversation(session_factory, settings, engine)
    state = ConversationState()
    opening = await service.handle(owner, "", context(owner, 0, new=True), state)
    transcript: list[tuple[str, ConversationReply]] = [("", opening)]
    state = opening.state
    for step, command in enumerate(script_commands(), start=1):
        reply = await service.handle(owner, command, context(owner, step), state)
        transcript.append((command, reply))
        state = reply.state
    return transcript


def spoken(transcript: list[tuple[str, ConversationReply]], command: str) -> str:
    for utterance, reply in transcript:
        if utterance == command:
            return reply.speech.text
    raise AssertionError(f"{command!r} is not in the script")


async def test_the_whole_lifecycle_answers_every_scripted_command(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    transcript = await run_script(session_factory, offline_settings, FakeEngine())

    unanswered = [command for command, reply in transcript if not reply.speech.text.strip()]
    assert unanswered == []
    # Every command in the shipped script is a command the skill really has.
    misunderstood = [command for command, reply in transcript if FALLBACK in reply.speech.text]
    assert misunderstood == []


async def test_each_mode_is_entered_and_left_in_one_dialogue(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    transcript = await run_script(session_factory, offline_settings, FakeEngine())

    assert "уровень 3" in spoken(transcript, "да")
    assert "Разделы справки" in spoken(transcript, "что ты умеешь")
    assert "Закрываю справку" in spoken(transcript, "выйти из справки")
    assert "подробнее" in spoken(transcript, "говори подробнее")
    assert "обе клетки" in spoken(transcript, "называй обе клетки")
    assert "e2e4" in spoken(transcript, "пешка е два е четыре")
    assert "3" in spoken(transcript, "какой сейчас уровень")
    assert spoken(transcript, "оцени позицию")
    assert spoken(transcript, "дай подсказку")
    assert spoken(transcript, "разбери партию")
    assert "1." in spoken(transcript, "pgn")
    assert "задач" in spoken(transcript, "выйти из задач")


async def test_the_finished_game_is_reviewed_without_being_changed(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    transcript = await run_script(session_factory, offline_settings, FakeEngine())
    game_id = transcript[-1][1].state.game_id
    assert game_id is not None

    with session_scope(session_factory) as session:
        finished = GameRepository(session).load(game_id, OWNER)

    assert finished.status is GameStatus.FINISHED
    # The trainer was switched off before the game ended, and neither the review
    # nor the puzzle may have re-moded or re-moved it.
    assert finished.mode is GameMode.GAME
    assert finished.moves[0] == "e2e4"
    assert "g1f3" in finished.moves


async def test_the_script_runs_identically_for_two_players(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    first = await run_script(session_factory, offline_settings, FakeEngine(), owner="e2e-owner-a")
    second = await run_script(session_factory, offline_settings, FakeEngine(), owner="e2e-owner-b")

    assert [reply.speech.text for _, reply in first] == [reply.speech.text for _, reply in second]
    assert first[-1][1].state.game_id != second[-1][1].state.game_id


async def test_an_analysis_timeout_never_costs_the_game_or_the_answer(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """Every coaching and review answer is optional; the game underneath is not."""
    engine = FakeEngine(analysis_timeout=True)

    transcript = await run_script(session_factory, offline_settings, engine)

    assert engine.analyses > 0
    assert [command for command, reply in transcript if not reply.speech.text.strip()] == []
    game_id = transcript[-1][1].state.game_id
    assert game_id is not None
    with session_scope(session_factory) as session:
        finished = GameRepository(session).load(game_id, OWNER)
    assert "e2e4" in finished.moves
    assert "g1f3" in finished.moves


async def test_the_script_needs_no_screen_and_reads_from_either_side(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    """The shell runs the same script with `--orientation white` and `black`."""
    transcript = await run_script(session_factory, offline_settings, FakeEngine())
    positions = [reply.turn.fen for _, reply in transcript if reply.turn is not None]
    assert positions

    for fen in positions:
        white = format_board(fen, chess.WHITE)
        black = format_board(fen, chess.BLACK)
        # Both orientations are renderable, and they are not the same picture.
        assert white and black
        assert white != black
        assert chess.Board(fen).fen() == fen
