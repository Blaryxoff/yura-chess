"""Tactical puzzles, solved by voice, entirely beside the game.

A puzzle is a packaged position and the forced line that solves it, so nothing
here needs the engine and nothing here may touch a game row: the UCI history,
the revision and any pending engine turn belong to the game alone. The only rows
this service writes are the attempt it is walking through and the difficulty
profile that attempt leaves behind.

Solving is a sequence of absolute positions rather than increments: the attempt
stores how many moves of the line have been applied, so the board a request is
judged against is derived, never remembered. That alone is not replay-safe — a
re-delivered move would be judged against the position its own first delivery
created — so every request that would change an attempt first claims the replay
key, exactly as a hint does. A re-delivered request reads the attempt back and
says where it stands, and counts no mistake, hint or result twice.

A move that is not the recorded solution is answered, never played: only the
line itself moves the board on. The one exception is the alternative mate, which
the source line cannot record but which solves the puzzle just as well.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, replace

import chess
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import PuzzleQuestion, PuzzleRequest
from yura_chess.application.game_service import RequestContext
from yura_chess.domain.puzzle import (
    CLEAN_SOLVES_TO_PROMOTE,
    FAILURES_TO_DEMOTE,
    Puzzle,
    PuzzleAttempt,
    PuzzleAttemptStatus,
    PuzzleProfile,
    catalogue,
)
from yura_chess.presentation.move_speech import PIECE_NAMES, Speech, describe_move
from yura_chess.presentation.position_speech import read_board
from yura_chess.storage.database import run_transaction_with_deadlock_retry, session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import RequestReplayRow
from yura_chess.storage.puzzle_repository import PuzzleRepository

# The same four escalating steps the trainer gives, counted per attempt.
MAX_PUZZLE_HINT = 4

_MORE = " Скажите «следующая задача» или «выйти из задач»."


@dataclass(frozen=True, slots=True)
class OpenPuzzle:
    """The puzzle an owner is on and how far into its line they have come."""

    puzzle: Puzzle
    attempt: PuzzleAttempt

    def board(self) -> chess.Board:
        """The position as the player sees it right now."""
        board = chess.Board(self.puzzle.fen)
        for uci in self.puzzle.moves[: _applied(self.attempt.node)]:
            board.push(chess.Move.from_uci(uci))
        return board

    @property
    def last_move(self) -> str | None:
        """The move that led to the position the player is looking at."""
        applied = _applied(self.attempt.node)
        return self.puzzle.moves[applied - 1] if applied else None

    @property
    def expected(self) -> str:
        """The move the player has to find in that position."""
        return self.puzzle.moves[_applied(self.attempt.node)]


@dataclass(frozen=True, slots=True)
class PuzzleReply:
    speech: Speech
    # False once the attempt is over, so the conversation stops intercepting.
    active: bool


class PuzzleService:
    """Run one owner's puzzle attempt; no game, and no engine, is involved."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        random_source: random.Random | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._random = random_source or random.Random()

    def find_open(self, owner_key: str) -> OpenPuzzle | None:
        """The unfinished puzzle this owner is on, if there is one."""
        with session_scope(self._session_factory) as session:
            attempt = PuzzleRepository(session).find_active_attempt(owner_key)
        if attempt is None:
            return None
        puzzle = _find_puzzle(attempt.puzzle_id)
        return OpenPuzzle(puzzle, attempt) if puzzle is not None else None

    def replayed_response(self, owner_key: str, context: RequestContext) -> PuzzleReply | None:
        """Return an exact stored puzzle answer before routing can reinterpret it."""
        with session_scope(self._session_factory) as session:
            replay = GameRepository(session).get_request_replay(
                context.skill_id,
                context.session_id,
                context.message_id,
                context.fingerprint,
                owner_key,
            )
            return _stored_reply(replay) if replay is not None else None

    def answer(
        self,
        owner_key: str,
        request: PuzzleRequest,
        context: RequestContext,
        open_puzzle: OpenPuzzle | None,
    ) -> PuzzleReply:
        """Answer one puzzle command; read-only questions never claim the request."""
        if request.question is PuzzleQuestion.STREAK:
            return self._streak(owner_key, open_puzzle is not None)
        if request.question is PuzzleQuestion.REPEAT:
            if open_puzzle is None:
                return PuzzleReply(Speech.of("Сейчас нет задачи. Скажите «дай задачу»."), active=False)
            return self.present(open_puzzle, lead="Повторяю. ")
        return run_transaction_with_deadlock_retry(
            self._session_factory,
            lambda session: self._answer_in_transaction(session, owner_key, request, context, open_puzzle),
        )

    def _answer_in_transaction(
        self,
        session: Session,
        owner_key: str,
        request: PuzzleRequest,
        context: RequestContext,
        open_puzzle: OpenPuzzle | None,
    ) -> PuzzleReply:
        games = GameRepository(session)
        replay, created = self._claim(games, owner_key, context)
        if not created:
            return _stored_reply(replay) or self._replayed(owner_key, PuzzleRepository(session))
        puzzles = PuzzleRepository(session)
        if request.question is PuzzleQuestion.EXIT:
            if open_puzzle is not None:
                self._close(puzzles, open_puzzle.attempt, showed_solution=False)
            reply = PuzzleReply(
                Speech.of("Выхожу из задач. Скажите «новая игра» или «продолжить»."),
                active=False,
            )
        elif request.question is PuzzleQuestion.SOLUTION:
            reply = (
                PuzzleReply(Speech.of("Сейчас нет задачи. Скажите «дай задачу»."), active=False)
                if open_puzzle is None
                else self._reveal(puzzles, open_puzzle)
            )
        else:
            reply = self._select(owner_key, puzzles, request, open_puzzle)
        return self._finalize(games, replay, reply)

    def play(self, owner_key: str, open_puzzle: OpenPuzzle, move_uci: str, context: RequestContext) -> PuzzleReply:
        """Judge one move against the recorded line; a wrong move is not played."""
        with session_scope(self._session_factory) as session:
            games = GameRepository(session)
            replay, created = self._claim(games, owner_key, context)
            if not created:
                return _stored_reply(replay) or self._replayed(owner_key, PuzzleRepository(session))
            puzzles = PuzzleRepository(session)
            board = open_puzzle.board()
            expected = chess.Move.from_uci(open_puzzle.expected)
            move = chess.Move.from_uci(move_uci)
            if move != expected and not _alternative_mate(board, expected, move):
                attempt = open_puzzle.attempt
                puzzles.advance(
                    attempt.owner_key,
                    attempt.puzzle_id,
                    attempt.revision,
                    node=attempt.node,
                    mistakes=attempt.mistakes + 1,
                    hints=attempt.hints,
                )
                reply = PuzzleReply(
                    Speech.of(
                        f"Ход {_phrase(board, move)} задачу не решает. "
                        "Назовите другой ход или скажите «покажи решение»."
                    ),
                    active=True,
                )
            else:
                reply = self._accept(puzzles, open_puzzle, board, move)
            return self._finalize(games, replay, reply)

    def hint(self, owner_key: str, open_puzzle: OpenPuzzle, context: RequestContext) -> PuzzleReply:
        """Four escalating hints; a re-delivered request advances none of them."""
        with session_scope(self._session_factory) as session:
            games = GameRepository(session)
            replay, created = self._claim(games, owner_key, context)
            if not created:
                return _stored_reply(replay) or self._replayed(owner_key, PuzzleRepository(session))
            puzzles = PuzzleRepository(session)
            attempt = open_puzzle.attempt
            stage = min(attempt.hints + 1, MAX_PUZZLE_HINT)
            puzzles.advance(
                attempt.owner_key,
                attempt.puzzle_id,
                attempt.revision,
                node=attempt.node,
                mistakes=attempt.mistakes,
                hints=stage,
            )
            board = open_puzzle.board()
            reply = PuzzleReply(
                Speech.of(_hint_text(board, chess.Move.from_uci(open_puzzle.expected), stage)),
                active=True,
            )
            return self._finalize(games, replay, reply)

    def abandon(self, owner_key: str, open_puzzle: OpenPuzzle) -> None:
        """Leave the puzzle behind because the player asked for a game instead."""
        with session_scope(self._session_factory) as session:
            self._close(PuzzleRepository(session), open_puzzle.attempt, showed_solution=False)

    @staticmethod
    def resume_prompt(open_puzzle: OpenPuzzle) -> Speech:
        """Ask about the unfinished puzzle, not about an unfinished game."""
        board = open_puzzle.board()
        side = "белых" if board.turn == chess.WHITE else "черных"
        return Speech.of(f"У вас есть нерешенная задача, ход {side}. Продолжить ее?")

    def present(self, open_puzzle: OpenPuzzle, lead: str = "") -> PuzzleReply:
        """Read the position out; the attempt itself is not touched."""
        board = open_puzzle.board()
        side = "белых" if board.turn == chess.WHITE else "черных"
        reading = read_board(board).speech.text
        return PuzzleReply(
            Speech.of(f"{lead}Задача, ход {side}. {reading} Назовите лучший ход."),
            active=True,
        )

    def _select(
        self,
        owner_key: str,
        repository: PuzzleRepository,
        request: PuzzleRequest,
        open_puzzle: OpenPuzzle | None,
    ) -> PuzzleReply:
        """Pick the next puzzle by theme, or by the difficulty this owner is at."""
        current = open_puzzle.puzzle.id if open_puzzle is not None else None
        # Skipping the open puzzle must never leave nothing to choose from.
        pool = [entry for entry in catalogue() if entry.id != current] or list(catalogue())
        if request.theme is not None:
            pool = [entry for entry in pool if request.theme in entry.themes]
            if not pool:
                return PuzzleReply(
                    Speech.of("Задач на эту тему у меня нет. Скажите «дай задачу»."),
                    active=open_puzzle is not None,
                )
        if open_puzzle is not None:
            # Asking for another puzzle is a skip, not one of the documented
            # demotion triggers. A wrong attempt only counts when the player
            # asks for the solution or explicitly exits puzzle mode.
            self._close(repository, open_puzzle.attempt, showed_solution=False, counted=False)
        if request.theme is None:
            profile = repository.load_profile(owner_key)
            # The bucket narrows the choice; it never empties it.
            pool = [entry for entry in pool if entry.bucket is profile.bucket] or pool
        chosen = self._random.choice(pool)
        attempt = repository.start_attempt(owner_key, chosen.id)
        return self.present(OpenPuzzle(chosen, attempt))

    def _accept(
        self,
        repository: PuzzleRepository,
        open_puzzle: OpenPuzzle,
        board: chess.Board,
        move: chess.Move,
    ) -> PuzzleReply:
        """Apply the found move and the forced reply that follows it."""
        puzzle = open_puzzle.puzzle
        index = _applied(open_puzzle.attempt.node)
        found = _phrase(board, move)
        if index + 1 >= len(puzzle.moves):
            return self._solved(repository, open_puzzle, f"Верно: {found}. Задача решена.")
        after = board.copy(stack=False)
        # Only the recorded move can continue a line, and an accepted move that
        # is not it is a mate, which never continues one.
        after.push(move)
        answer = _phrase(after, chess.Move.from_uci(puzzle.moves[index + 1]))
        attempt = open_puzzle.attempt
        if index + 2 >= len(puzzle.moves):
            return self._solved(repository, open_puzzle, f"Верно: {found}. Я отвечаю {answer}, и задача решена.")
        repository.advance(
            attempt.owner_key,
            attempt.puzzle_id,
            attempt.revision,
            node=index + 2,
            mistakes=attempt.mistakes,
            hints=attempt.hints,
        )
        return PuzzleReply(Speech.of(f"Верно: {found}. Я отвечаю {answer}. Ваш ход."), active=True)

    def _solved(self, repository: PuzzleRepository, open_puzzle: OpenPuzzle, lead: str) -> PuzzleReply:
        clean = open_puzzle.attempt.mistakes == 0 and open_puzzle.attempt.hints == 0
        profile = self._finish(repository, open_puzzle.attempt, PuzzleAttemptStatus.SOLVED, solved=True, clean=clean)
        series = f" Ваша серия: {profile.clean_streak}." if clean else ""
        return PuzzleReply(Speech.of(f"{lead}{series}{_MORE}"), active=False)

    def _reveal(self, repository: PuzzleRepository, open_puzzle: OpenPuzzle) -> PuzzleReply:
        """Say the whole remaining line and close the attempt as a failure."""
        board = open_puzzle.board()
        phrases = []
        for uci in open_puzzle.puzzle.moves[_applied(open_puzzle.attempt.node) :]:
            move = chess.Move.from_uci(uci)
            phrases.append(_phrase(board, move))
            board.push(move)
        self._finish(repository, open_puzzle.attempt, PuzzleAttemptStatus.FAILED, solved=False, clean=False)
        return PuzzleReply(Speech.of(f"Решение: {', '.join(phrases)}.{_MORE}"), active=False)

    def _streak(self, owner_key: str, active: bool) -> PuzzleReply:
        with session_scope(self._session_factory) as session:
            profile = PuzzleRepository(session).load_profile(owner_key)
        return PuzzleReply(
            Speech.of(
                f"Подряд без ошибок и подсказок решено задач: {profile.clean_streak}. "
                f"Сложность сейчас {_BUCKET_NAMES[profile.bucket]}."
            ),
            active=active,
        )

    def _replayed(self, owner_key: str, repository: PuzzleRepository) -> PuzzleReply:
        """A re-delivered request changes nothing and says where the attempt stands."""
        attempt = repository.find_active_attempt(owner_key)
        open_puzzle = _open_attempt(attempt) if attempt is not None else None
        if open_puzzle is None:
            return PuzzleReply(Speech.of(f"Эта задача уже закончена.{_MORE}"), active=False)
        return self.present(open_puzzle)

    def _close(
        self,
        repository: PuzzleRepository,
        attempt: PuzzleAttempt,
        showed_solution: bool,
        counted: bool | None = None,
    ) -> None:
        """Leaving after a wrong move is a failure; leaving a clean attempt is not."""
        failed = showed_solution or attempt.mistakes > 0
        counts = failed if counted is None else counted
        status = PuzzleAttemptStatus.FAILED if counts else PuzzleAttemptStatus.ABANDONED
        self._finish(repository, attempt, status, solved=False, clean=False, counted=counts)

    def _finish(
        self,
        repository: PuzzleRepository,
        attempt: PuzzleAttempt,
        status: PuzzleAttemptStatus,
        solved: bool,
        clean: bool,
        counted: bool = True,
    ) -> PuzzleProfile:
        """Close the attempt and store the difficulty it leaves, in one flush."""
        profile = repository.load_profile(attempt.owner_key, for_update=True)
        if counted:
            profile = _next_profile(profile, solved=solved, clean=clean)
        _, stored = repository.finish_attempt(
            attempt.owner_key,
            attempt.puzzle_id,
            attempt.revision,
            status,
            profile,
        )
        return stored

    @staticmethod
    def _claim(
        repository: GameRepository,
        owner_key: str,
        context: RequestContext,
    ) -> tuple[RequestReplayRow, bool]:
        """Claim the replay key; `False` means this request was already applied.

        The claim carries no game id: a puzzle is not a turn, and nothing may
        later resume it as one.
        """
        return repository.record_request(
            context.skill_id,
            context.session_id,
            context.message_id,
            context.fingerprint,
            owner_key,
        )

    @staticmethod
    def _finalize(
        repository: GameRepository,
        replay: RequestReplayRow,
        reply: PuzzleReply,
    ) -> PuzzleReply:
        repository.store_response(replay, _reply_payload(reply))
        return reply


_BUCKET_NAMES = {
    "low": "простая",
    "medium": "средняя",
    "high": "высокая",
}


def _reply_payload(reply: PuzzleReply) -> str:
    return json.dumps(
        {
            "kind": "puzzle_reply",
            "text": reply.speech.text,
            "tts": reply.speech.tts,
            "active": reply.active,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _stored_reply(replay: RequestReplayRow) -> PuzzleReply | None:
    if replay.response_payload is None:
        return None
    try:
        payload = json.loads(replay.response_payload)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "puzzle_reply":
        return None
    text = payload.get("text")
    tts = payload.get("tts")
    active = payload.get("active")
    if not isinstance(text, str) or (tts is not None and not isinstance(tts, str)) or not isinstance(active, bool):
        return None
    return PuzzleReply(Speech(text, tts), active)


def _open_attempt(attempt: PuzzleAttempt) -> OpenPuzzle | None:
    puzzle = _find_puzzle(attempt.puzzle_id)
    return OpenPuzzle(puzzle, attempt) if puzzle is not None else None


def _next_profile(profile: PuzzleProfile, solved: bool, clean: bool) -> PuzzleProfile:
    """Where a finished attempt leaves the difficulty and the two streaks.

    The streaks keep counting past the step they trigger, so the series the
    player is told is the series they are actually on.
    """
    if solved and clean:
        streak = profile.clean_streak + 1
        bucket = profile.bucket.harder() if streak % CLEAN_SOLVES_TO_PROMOTE == 0 else profile.bucket
        return replace(profile, bucket=bucket, clean_streak=streak, failure_streak=0)
    if solved:
        # Solved with a hint or after a mistake: not a clean run, but it does
        # break the run of failures that demotes the difficulty.
        return replace(profile, clean_streak=0, failure_streak=0)
    failures = profile.failure_streak + 1
    bucket = profile.bucket.easier() if failures % FAILURES_TO_DEMOTE == 0 else profile.bucket
    return replace(profile, bucket=bucket, clean_streak=0, failure_streak=failures)


def _find_puzzle(puzzle_id: str) -> Puzzle | None:
    for entry in catalogue():
        if entry.id == puzzle_id:
            return entry
    return None


def _applied(node: int) -> int:
    """How many moves of the line are on the board.

    A fresh attempt stores nothing, but the move that creates the position is
    always applied: it is the opponent's, not something the player has to find.
    """
    return node or 1


def _alternative_mate(board: chess.Board, expected: chess.Move, move: chess.Move) -> bool:
    """A different mate solves a mate just as well; nothing else is an alternative."""
    return _mates(board, expected) and _mates(board, move)


def _mates(board: chess.Board, move: chess.Move) -> bool:
    after = board.copy(stack=False)
    after.push(move)
    return after.is_checkmate()


def _phrase(board: chess.Board, move: chess.Move) -> str:
    """Name a move as a phrase, without the full stop that ends a sentence."""
    return describe_move(board, move).text.rstrip(".")


def _hint_text(board: chess.Board, move: chess.Move, stage: int) -> str:
    again = " Скажите «подскажи» еще раз."
    if stage <= 1:
        return f"Подсказка: {_idea(board, move)}.{again}"
    if stage == 2:
        piece = board.piece_at(move.from_square)
        name = PIECE_NAMES[piece.piece_type] if piece is not None else "фигура"
        return f"Подсказка: ходит {name}.{again}"
    if stage == 3:
        return f"Подсказка: поле назначения — {chess.square_name(move.to_square)}.{again}"
    return f"Полная подсказка: {_phrase(board, move)}."


def _idea(board: chess.Board, move: chess.Move) -> str:
    """The first hint: what kind of move solves it, not which one."""
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return "решение — мат"
    if after.is_check():
        return "решение начинается с шаха"
    if board.is_capture(move):
        return "решение начинается со взятия"
    if move.promotion is not None:
        return "решает превращение пешки"
    return "решает тихий ход, а не взятие"
