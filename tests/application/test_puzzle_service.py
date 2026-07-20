"""Voice puzzles — always against a real MariaDB, and never against a game."""

from __future__ import annotations

import random

import chess
import pytest
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import CommandKind, PuzzleQuestion, PuzzleRequest, route
from yura_chess.application.conversation import ConversationService
from yura_chess.application.game_service import RequestContext
from yura_chess.application.puzzle_service import OpenPuzzle, PuzzleService
from yura_chess.domain.analysis import PositionAnalysis
from yura_chess.domain.game import PlayerColor
from yura_chess.domain.puzzle import Puzzle, PuzzleAttemptStatus, PuzzleBucket, PuzzleProfile, catalogue
from yura_chess.settings import Settings
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.puzzle_repository import PuzzleRepository

pytestmark = pytest.mark.anyio

OWNER = "p" * 64

# Shipped catalogue entries the tests drive end to end.
# All of them sit in the default `medium` band, so a plain request can reach them.
MATE_IN_ONE = "001cr"
MATE_IN_TWO = "000hf"
LONG_LINE = "00008"


class FakeEngine:
    """Plays the first legal move; a puzzle never asks it for anything else."""

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        return next(iter(board.legal_moves)).uci()

    async def analyse(
        self,
        board: chess.Board,
        search_time: float | None = None,
        candidates: int | None = None,
    ) -> PositionAnalysis:
        return PositionAnalysis(
            fen=board.fen(),
            side_to_move=PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK,
            depth=1,
            candidates=(),
        )


class FixedChoice(random.Random):
    """Picks the puzzle the test named, so selection is not a coin toss."""

    def __init__(self, *puzzle_ids: str) -> None:
        super().__init__(0)
        self._wanted = list(puzzle_ids)

    def choice(self, seq):  # type: ignore[override] # noqa: ANN001, ANN201 - Sequence[Puzzle]
        wanted = self._wanted.pop(0) if self._wanted else None
        if wanted is None:
            return seq[0]
        return next(entry for entry in seq if entry.id == wanted)


def context(message_id: int, *, new: bool = False) -> RequestContext:
    value = f"p{message_id}"
    return RequestContext("shell", "puzzles", value, value.ljust(64, "0"), new)


def puzzle(puzzle_id: str) -> Puzzle:
    return next(entry for entry in catalogue() if entry.id == puzzle_id)


def service(session_factory: sessionmaker[Session], *puzzle_ids: str) -> PuzzleService:
    return PuzzleService(session_factory, FixedChoice(*puzzle_ids))


def open_puzzle(service: PuzzleService, owner_key: str = OWNER) -> OpenPuzzle:
    found = service.find_open(owner_key)
    assert found is not None
    return found


def attempt(session_factory: sessionmaker[Session], puzzle_id: str):  # noqa: ANN201 - PuzzleAttempt | None
    with session_scope(session_factory) as session:
        return PuzzleRepository(session).find_attempt(OWNER, puzzle_id)


def profile(session_factory: sessionmaker[Session]) -> PuzzleProfile:
    with session_scope(session_factory) as session:
        return PuzzleRepository(session).load_profile(OWNER)


def store_profile(session_factory: sessionmaker[Session], stored: PuzzleProfile) -> None:
    with session_scope(session_factory) as session:
        PuzzleRepository(session).save_profile(stored)


def start(service: PuzzleService, message_id: int, theme: str | None = None) -> None:
    service.answer(OWNER, PuzzleRequest(PuzzleQuestion.START, theme=theme), context(message_id), None)


def test_a_mate_in_one_is_solved_by_the_single_recorded_move(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_ONE)
    start(puzzles, 1)

    reply = puzzles.play(OWNER, open_puzzle(puzzles), "d7e8", context(2))

    assert "решена" in reply.speech.text
    assert reply.active is False
    assert attempt(session_factory, MATE_IN_ONE).status is PuzzleAttemptStatus.SOLVED
    assert profile(session_factory).clean_streak == 1


def test_a_mate_in_two_is_solved_through_its_forced_reply(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    start(puzzles, 1)

    first = puzzles.play(OWNER, open_puzzle(puzzles), "e2e6", context(2))

    assert "Я отвечаю" in first.speech.text
    assert first.active is True
    # The forced reply is applied with the move it answers: the player is asked
    # for the next move of the line, not for the same one again.
    assert attempt(session_factory, MATE_IN_TWO).node == 3

    second = puzzles.play(OWNER, open_puzzle(puzzles), "e6f7", context(3))

    assert "решена" in second.speech.text
    assert attempt(session_factory, MATE_IN_TWO).status is PuzzleAttemptStatus.SOLVED


def test_a_long_line_asks_for_every_player_move_in_turn(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, LONG_LINE)
    start(puzzles, 1)
    entry = puzzle(LONG_LINE)

    nodes = []
    for step, index in enumerate((1, 3, 5), start=2):
        current = open_puzzle(puzzles)
        assert current.expected == entry.moves[index]
        puzzles.play(OWNER, current, entry.moves[index], context(step))
        nodes.append(attempt(session_factory, LONG_LINE).node)

    assert nodes == [3, 5, 5]
    assert attempt(session_factory, LONG_LINE).status is PuzzleAttemptStatus.SOLVED


def test_a_different_mate_solves_a_mate_puzzle(session_factory: sessionmaker[Session]) -> None:
    """The source line records one mate; another one is just as good a solution."""
    puzzles = service(session_factory)
    synthetic = Puzzle(
        id="synthetic-mate",
        fen="6k1/1p3ppp/8/8/8/8/8/R3R1K1 b - - 0 1",
        moves=("b7b6", "a1a8"),
        rating=800,
        themes=("mateIn1",),
        bucket=PuzzleBucket.LOW,
    )
    with session_scope(session_factory) as session:
        started = PuzzleRepository(session).start_attempt(OWNER, synthetic.id)

    reply = puzzles.play(OWNER, OpenPuzzle(synthetic, started), "e1e8", context(1))

    assert "решена" in reply.speech.text
    assert attempt(session_factory, synthetic.id).status is PuzzleAttemptStatus.SOLVED


def test_a_legal_wrong_move_is_counted_once_and_never_played(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    start(puzzles, 1)

    reply = puzzles.play(OWNER, open_puzzle(puzzles), "b3b4", context(2))

    assert "не решает" in reply.speech.text
    assert reply.active is True
    stored = attempt(session_factory, MATE_IN_TWO)
    assert (stored.node, stored.mistakes, stored.status) == (0, 1, PuzzleAttemptStatus.ACTIVE)


def test_a_replayed_wrong_move_counts_no_second_mistake(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    start(puzzles, 1)
    puzzles.play(OWNER, open_puzzle(puzzles), "b3b4", context(2))
    before = attempt(session_factory, MATE_IN_TWO)

    puzzles.play(OWNER, open_puzzle(puzzles), "b3b4", context(2))

    after = attempt(session_factory, MATE_IN_TWO)
    assert (after.mistakes, after.revision) == (before.mistakes, before.revision)


def test_a_replayed_correct_move_does_not_advance_the_line_twice(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    start(puzzles, 1)
    first = open_puzzle(puzzles)
    puzzles.play(OWNER, first, "e2e6", context(2))
    before = attempt(session_factory, MATE_IN_TWO)

    # The same delivery, judged against the position it created: without the
    # replay claim it would be read as a wrong move.
    replayed = puzzles.play(OWNER, open_puzzle(puzzles), "e2e6", context(2))

    after = attempt(session_factory, MATE_IN_TWO)
    assert (after.node, after.mistakes, after.revision) == (before.node, before.mistakes, before.revision)
    assert "не решает" not in replayed.speech.text


def test_hints_escalate_once_per_request(session_factory: sessionmaker[Session]) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    start(puzzles, 1)

    texts = [puzzles.hint(OWNER, open_puzzle(puzzles), context(step)).speech.text for step in range(2, 6)]
    replayed = puzzles.hint(OWNER, open_puzzle(puzzles), context(5))

    assert "шаха" in texts[0]
    assert "ферзь" in texts[1]
    assert "e6" in texts[2]
    assert "Полная подсказка" in texts[3]
    # The fifth delivery is the fourth request again: it neither escalates nor
    # counts a fifth hint.
    assert replayed.speech.text == texts[3]
    assert attempt(session_factory, MATE_IN_TWO).hints == 4


def test_the_solution_is_read_out_and_closes_the_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    start(puzzles, 1)

    reply = puzzles.answer(OWNER, PuzzleRequest(PuzzleQuestion.SOLUTION), context(2), open_puzzle(puzzles))

    assert reply.speech.text.startswith("Решение:")
    assert reply.active is False
    assert attempt(session_factory, MATE_IN_TWO).status is PuzzleAttemptStatus.FAILED


def test_three_clean_solves_raise_the_difficulty_by_one_step(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_ONE, MATE_IN_ONE, MATE_IN_ONE)

    for step in range(3):
        start(puzzles, step * 2 + 1)
        solved = puzzles.play(OWNER, open_puzzle(puzzles), "d7e8", context(step * 2 + 2))
        # The announced series is the one the player is on, promotion or not.
        assert f"Ваша серия: {step + 1}." in solved.speech.text
        if step < 2:
            assert profile(session_factory).bucket is PuzzleBucket.MEDIUM

    assert profile(session_factory).bucket is PuzzleBucket.HIGH
    assert profile(session_factory).clean_streak == 3


def test_a_solve_with_a_hint_does_not_count_towards_the_clean_streak(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_ONE)
    start(puzzles, 1)
    puzzles.hint(OWNER, open_puzzle(puzzles), context(2))

    puzzles.play(OWNER, open_puzzle(puzzles), "d7e8", context(3))

    stored = profile(session_factory)
    assert (stored.clean_streak, stored.bucket) == (0, PuzzleBucket.MEDIUM)


def test_two_failures_lower_the_difficulty_by_one_step(session_factory: sessionmaker[Session]) -> None:
    puzzles = service(session_factory, MATE_IN_TWO, MATE_IN_ONE)

    start(puzzles, 1)
    puzzles.answer(OWNER, PuzzleRequest(PuzzleQuestion.SOLUTION), context(2), open_puzzle(puzzles))
    assert profile(session_factory).bucket is PuzzleBucket.MEDIUM

    start(puzzles, 3)
    # Leaving after a wrong move is the second failure, even without a solution.
    puzzles.play(OWNER, open_puzzle(puzzles), "b2b3", context(4))
    puzzles.answer(OWNER, PuzzleRequest(PuzzleQuestion.EXIT), context(5), open_puzzle(puzzles))

    stored = profile(session_factory)
    assert (stored.bucket, stored.failure_streak) == (PuzzleBucket.LOW, 2)


def test_leaving_a_clean_attempt_changes_no_difficulty(session_factory: sessionmaker[Session]) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    start(puzzles, 1)

    reply = puzzles.answer(OWNER, PuzzleRequest(PuzzleQuestion.EXIT), context(2), open_puzzle(puzzles))

    assert reply.active is False
    assert attempt(session_factory, MATE_IN_TWO).status is PuzzleAttemptStatus.ABANDONED
    stored = profile(session_factory)
    assert (stored.bucket, stored.failure_streak, stored.clean_streak) == (PuzzleBucket.MEDIUM, 0, 0)


def test_the_difficulty_never_leaves_the_shipped_bands(session_factory: sessionmaker[Session]) -> None:
    puzzles = service(session_factory)

    for step in range(4):
        start(puzzles, step * 2 + 1)
        puzzles.answer(OWNER, PuzzleRequest(PuzzleQuestion.SOLUTION), context(step * 2 + 2), open_puzzle(puzzles))

    assert profile(session_factory).bucket is PuzzleBucket.LOW


def test_a_theme_request_picks_a_puzzle_of_that_theme(session_factory: sessionmaker[Session]) -> None:
    puzzles = PuzzleService(session_factory, random.Random(7))
    store_profile(session_factory, PuzzleProfile(OWNER, bucket=PuzzleBucket.HIGH))

    start(puzzles, 1, theme="mateIn1")

    assert "mateIn1" in open_puzzle(puzzles).puzzle.themes


def test_a_plain_request_picks_a_puzzle_of_the_stored_difficulty(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = PuzzleService(session_factory, random.Random(11))
    store_profile(session_factory, PuzzleProfile(OWNER, bucket=PuzzleBucket.HIGH))

    start(puzzles, 1)

    assert open_puzzle(puzzles).puzzle.bucket is PuzzleBucket.HIGH


def test_the_next_puzzle_is_a_different_one(session_factory: sessionmaker[Session]) -> None:
    puzzles = PuzzleService(session_factory, random.Random(3))
    start(puzzles, 1)
    first = open_puzzle(puzzles).puzzle.id

    puzzles.answer(OWNER, PuzzleRequest(PuzzleQuestion.NEXT), context(2), open_puzzle(puzzles))

    assert open_puzzle(puzzles).puzzle.id != first


def test_the_series_is_reported_without_touching_the_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    puzzles = service(session_factory, MATE_IN_ONE)
    start(puzzles, 1)
    before = attempt(session_factory, MATE_IN_ONE)

    reply = puzzles.answer(OWNER, PuzzleRequest(PuzzleQuestion.STREAK), context(2), open_puzzle(puzzles))

    assert "серия" in reply.speech.text or "решено задач" in reply.speech.text
    assert reply.active is True
    assert attempt(session_factory, MATE_IN_ONE).revision == before.revision


def test_puzzles_are_isolated_between_owners(session_factory: sessionmaker[Session]) -> None:
    other = "q" * 64
    puzzles = service(session_factory, MATE_IN_ONE)
    start(puzzles, 1)

    assert puzzles.find_open(other) is None


async def test_a_puzzle_move_never_touches_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings, puzzles)
    started = await conversation.handle(OWNER, "", context(1))
    game_id = started.state.game_id or ""
    with session_scope(session_factory) as session:
        before = GameRepository(session).load(game_id, OWNER)

    await conversation.handle(OWNER, "дай задачу", context(2), started.state)
    wrong = await conversation.handle(OWNER, "пешка бэ четыре", context(3), started.state)

    with session_scope(session_factory) as session:
        after = GameRepository(session).load(game_id, OWNER)
    assert "не решает" in wrong.speech.text
    assert (after.moves, after.revision, after.pending_engine_turn) == (
        before.moves,
        before.revision,
        before.pending_engine_turn,
    )


async def test_an_illegal_puzzle_move_is_explained_and_an_ambiguous_one_asked_about(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings, puzzles)
    opened = await conversation.handle(OWNER, "дай задачу", context(1, new=True))

    illegal = await conversation.handle(OWNER, "ладья на а восемь", context(2), opened.state)
    ambiguous = await conversation.handle(OWNER, "ладья на е один", context(3), opened.state)

    assert "не решает" not in illegal.speech.text
    assert "неоднозначен" in ambiguous.speech.text
    assert ambiguous.state.clarification is not None
    stored = attempt(session_factory, MATE_IN_TWO)
    assert (stored.node, stored.mistakes) == (0, 0)


async def test_a_game_command_leaves_the_puzzle_and_reaches_the_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings, puzzles)
    opened = await conversation.handle(OWNER, "дай задачу", context(1, new=True))

    started = await conversation.handle(OWNER, "новая игра", context(2), opened.state)

    assert "Новая партия" in started.speech.text
    assert puzzles.find_open(OWNER) is None


async def test_an_unfinished_puzzle_is_resumed_as_a_puzzle_not_as_a_game(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings, puzzles)
    started = await conversation.handle(OWNER, "", context(1))
    await conversation.handle(OWNER, "дай задачу", context(2), started.state)

    prompt = await conversation.handle(OWNER, "", context(3, new=True), started.state)
    resumed = await conversation.handle(OWNER, "да", context(4), prompt.state)

    assert "задача" in prompt.speech.text
    assert "партия" not in prompt.speech.text
    assert "Задача, ход" in resumed.speech.text


async def test_declining_the_resumed_puzzle_gives_the_game_back(
    session_factory: sessionmaker[Session],
    offline_settings: Settings,
) -> None:
    puzzles = service(session_factory, MATE_IN_TWO)
    conversation = ConversationService(session_factory, FakeEngine(), offline_settings, puzzles)
    started = await conversation.handle(OWNER, "", context(1))
    await conversation.handle(OWNER, "дай задачу", context(2), started.state)
    prompt = await conversation.handle(OWNER, "", context(3, new=True), started.state)

    declined = await conversation.handle(OWNER, "нет", context(4), prompt.state)
    played = await conversation.handle(OWNER, "пешка е четыре", context(5), declined.state)

    assert puzzles.find_open(OWNER) is None
    assert "Ваш ход: e2e4" in played.speech.text


def test_puzzle_commands_are_routed_before_the_game_commands() -> None:
    assert route("дай задачу").kind is CommandKind.PUZZLE
    assert route("еще задачу").kind is CommandKind.PUZZLE
    assert route("выйти из задач").puzzle == PuzzleRequest(PuzzleQuestion.EXIT)
    assert route("покажи решение").puzzle == PuzzleRequest(PuzzleQuestion.SOLUTION)
    assert route("задача на мат в два").puzzle == PuzzleRequest(PuzzleQuestion.START, theme="mateIn2")
    assert route("следующая задача на вилку").puzzle == PuzzleRequest(PuzzleQuestion.NEXT, theme="fork")
    # A game command that merely mentions another game stays a game command.
    assert route("сыграем еще партию").kind is CommandKind.REMATCH
    assert route("сдаюсь", chess.Board()).kind is CommandKind.RESIGN
