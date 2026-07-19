"""Decide what an utterance means before anything touches the game.

Control commands, position questions and chess moves are separated here, in that
order: a phrase like «сдаюсь» must never reach move resolution. The router is
pure — it reads the position and the clarification carried in from the previous
turn, and returns the clarification the next turn should carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import chess

from yura_chess.voice.illegal_move import Explanation, explain
from yura_chess.voice.move_resolver import resolve
from yura_chess.voice.normalizer import normalize
from yura_chess.voice.types import MoveResolution, Normalized, ResolutionStatus

DEFAULT_CONFIDENCE_THRESHOLD = 0.7


class CommandKind(StrEnum):
    START = "start"
    NEW_GAME = "new_game"
    CONTINUE = "continue"
    RESIGN = "resign"
    CLAIM_DRAW = "claim_draw"
    UNDO = "undo"
    LEVEL_QUERY = "level_query"
    POSITION_QUERY = "position_query"
    # «что ты услышала» — replays the previous normalised utterance.
    REPEAT_HEARD = "repeat_heard"
    REPEAT_SLOW = "repeat_slow"
    HELP = "help"
    MOVE = "move"
    # A move was understood but is not legal in the current position.
    ILLEGAL_MOVE = "illegal_move"
    # A move was understood but not certainly enough to play it.
    CLARIFY = "clarify"
    CANCEL_CLARIFY = "cancel_clarify"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PendingClarification:
    """What the skill is waiting to have confirmed or narrowed down."""

    heard: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutedCommand:
    kind: CommandKind
    normalized: Normalized
    move: str | None = None
    resolution: MoveResolution | None = None
    # Carried into the next turn; `None` clears a clarification that is over.
    clarification: PendingClarification | None = None
    # What «что ты услышала» answers with, i.e. the previous turn's utterance.
    heard: str | None = None
    # Why the described move cannot be played; set only for `ILLEGAL_MOVE`.
    explanation: Explanation | None = None


_CONTROL_PATTERNS: tuple[tuple[CommandKind, re.Pattern[str]], ...] = (
    (CommandKind.REPEAT_HEARD, re.compile(r"что (ты )?(услышал|поняла|понял|разобрал)|что я сказал")),
    (
        CommandKind.REPEAT_SLOW,
        re.compile(r"^повтори( еще раз)? медленн(о|ее)|^повтори (последнюю фразу|ответ)$"),
    ),
    (CommandKind.NEW_GAME, re.compile(r"нов(ая|ую) (игра|игру|партия|партию)|начн?ем заново|сначала|заново")),
    (CommandKind.RESIGN, re.compile(r"сдаюсь|сдаться|сдаемся|я проиграл")),
    (CommandKind.CLAIM_DRAW, re.compile(r"ничь(я|ю|ей)")),
    (CommandKind.UNDO, re.compile(r"отмен(и|ить|яю)|верни(?: последний)?(?: ход)?|^ход назад$|переходить")),
    (CommandKind.HELP, re.compile(r"помощь|что ты умеешь|справка|как играть")),
    (CommandKind.START, re.compile(r"начать игру|начн?ем игру|давай играть|поехали|старт")),
    (CommandKind.CONTINUE, re.compile(r"продолж")),
    (
        CommandKind.LEVEL_QUERY,
        re.compile(
            r"какой( сейчас)? уровень|какая( сейчас)? сложность|текущ(ий уровень|ая сложность)|"
            r"на каком уровне|^уровень сложности$"
        ),
    ),
    (
        CommandKind.POSITION_QUERY,
        re.compile(
            r"кака(я|ю) позици|позици(я|ю)|\bгде\b|что на|покажи доску|какие фигуры|прочитай|"
            r"чей ход|кто ходит|кому ходить|моя очередь|есть ли шах|кто под шахом|шах сейчас|"
            r"последн(ий|его) ход|как (ты|я) походил|ход(а|ов)? назад|раз(а)? назад|"
            r"что (сделали|делали) (белые|черные)|^(дальше|далее)$"
        ),
    ),
)

_AFFIRM = re.compile(r"^(да|ага|верно|точно|правильно|подтверждаю)$")
_DECLINE = re.compile(r"^(нет|не|отмена|неверно|неправильно)$")


def confirmation_answer(utterance: str) -> bool | None:
    """Return a bare yes/no answer, or ``None`` for any other utterance."""
    text = normalize(utterance).text
    if _AFFIRM.match(text):
        return True
    if _DECLINE.match(text):
        return False
    return None


def route(
    utterance: str,
    board: chess.Board | None = None,
    pending: PendingClarification | None = None,
    last_heard: str | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> RoutedCommand:
    """Classify `utterance`; `board` is `None` when there is no game to move in."""
    normalized = normalize(utterance)

    for kind, pattern in _CONTROL_PATTERNS:
        if pattern.search(normalized.text):
            heard = last_heard if kind is CommandKind.REPEAT_HEARD else None
            # A control command answers the clarification by replacing it.
            return RoutedCommand(kind, normalized, heard=heard, clarification=None)

    if pending is not None:
        answered = _answer_clarification(normalized, pending)
        if answered is not None:
            return answered

    if board is None:
        return RoutedCommand(CommandKind.UNKNOWN, normalized)

    resolution = resolve(normalized, board)
    return _from_resolution(normalized, resolution, board, confidence_threshold)


def _answer_clarification(normalized: Normalized, pending: PendingClarification) -> RoutedCommand | None:
    """Handle only a bare yes/no; anything else is re-read as a fresh utterance."""
    if _DECLINE.match(normalized.text):
        return RoutedCommand(CommandKind.CANCEL_CLARIFY, normalized, clarification=None)
    if _AFFIRM.match(normalized.text):
        if len(pending.candidates) == 1:
            return RoutedCommand(CommandKind.MOVE, normalized, move=pending.candidates[0], clarification=None)
        # «да» cannot pick between several candidates; keep waiting.
        return RoutedCommand(CommandKind.CLARIFY, normalized, clarification=pending)
    return None


def _from_resolution(
    normalized: Normalized,
    resolution: MoveResolution,
    board: chess.Board,
    confidence_threshold: float,
) -> RoutedCommand:
    if resolution.status is ResolutionStatus.RESOLVED and resolution.confidence >= confidence_threshold:
        return RoutedCommand(CommandKind.MOVE, normalized, move=resolution.move, resolution=resolution)
    if resolution.status is ResolutionStatus.UNMATCHED:
        if resolution.recognized.is_empty:
            return RoutedCommand(CommandKind.UNKNOWN, normalized, resolution=resolution)
        # Nothing legal matched a move the player did describe: say why, rather
        # than asking them to repeat a move that would stay illegal.
        return RoutedCommand(
            CommandKind.ILLEGAL_MOVE,
            normalized,
            resolution=resolution,
            explanation=explain(resolution.recognized, board),
        )
    # Ambiguous and low-confidence readings wait for the player instead of
    # touching the game.
    return RoutedCommand(
        CommandKind.CLARIFY,
        normalized,
        resolution=resolution,
        clarification=PendingClarification(heard=normalized.text, candidates=resolution.candidates),
    )
