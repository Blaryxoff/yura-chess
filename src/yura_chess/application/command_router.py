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
    POSITION_QUERY = "position_query"
    # «что ты услышала» — replays the previous normalised utterance.
    REPEAT_HEARD = "repeat_heard"
    HELP = "help"
    MOVE = "move"
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


_CONTROL_PATTERNS: tuple[tuple[CommandKind, re.Pattern[str]], ...] = (
    (CommandKind.REPEAT_HEARD, re.compile(r"что (ты )?(услышал|поняла|понял|разобрал)|что я сказал")),
    (CommandKind.NEW_GAME, re.compile(r"нов(ая|ую) (игра|игру|партия|партию)|начн?ем заново|сначала|заново")),
    (CommandKind.RESIGN, re.compile(r"сдаюсь|сдаться|сдаемся|я проиграл")),
    (CommandKind.CLAIM_DRAW, re.compile(r"ничь(я|ю|ей)")),
    (CommandKind.UNDO, re.compile(r"отмен(и|ить|яю)|верни|назад|переходить")),
    (CommandKind.HELP, re.compile(r"помощь|что ты умеешь|справка|как играть")),
    (CommandKind.START, re.compile(r"начать игру|начн?ем игру|давай играть|поехали|старт")),
    (CommandKind.CONTINUE, re.compile(r"продолж")),
    (
        CommandKind.POSITION_QUERY,
        re.compile(r"кака(я|ю) позици|позици(я|ю)|где сто|где мой|что на|покажи доску|какие фигуры|прочитай"),
    ),
)

_AFFIRM = re.compile(r"^(да|ага|верно|точно|правильно|подтверждаю)$")
_DECLINE = re.compile(r"^(нет|не|отмена|неверно|неправильно)$")


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
    return _from_resolution(normalized, resolution, confidence_threshold)


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
    confidence_threshold: float,
) -> RoutedCommand:
    if resolution.status is ResolutionStatus.RESOLVED and resolution.confidence >= confidence_threshold:
        return RoutedCommand(CommandKind.MOVE, normalized, move=resolution.move, resolution=resolution)
    if resolution.status is ResolutionStatus.UNMATCHED and resolution.recognized.is_empty:
        return RoutedCommand(CommandKind.UNKNOWN, normalized, resolution=resolution)
    # Ambiguous, low-confidence and reconstructible-but-illegal all wait for the
    # player instead of touching the game.
    return RoutedCommand(
        CommandKind.CLARIFY,
        normalized,
        resolution=resolution,
        clarification=PendingClarification(heard=normalized.text, candidates=resolution.candidates),
    )
