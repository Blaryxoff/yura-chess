"""Decide what an utterance means before anything touches the game.

Control commands, position questions and chess moves are separated here, in that
order: a phrase like «сдаюсь» must never reach move resolution. The router is
pure — it reads the position and the clarification carried in from the previous
turn, and returns the clarification the next turn should carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

import chess

from yura_chess.domain.preferences import (
    BoardOrientation,
    DetailLevel,
    NotationStyle,
    PauseStyle,
    PlayerPreferences,
)
from yura_chess.presentation import game_facts
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
    # A question about the game itself: colour, move number, captures, castling.
    GAME_FACT = "game_fact"
    POSITION_QUERY = "position_query"
    # «что ты услышала» — replays the previous normalised utterance.
    REPEAT_HEARD = "repeat_heard"
    REPEAT_SLOW = "repeat_slow"
    HELP = "help"
    HELP_EXIT = "help_exit"
    # A durable presentation setting: how much is said, how, and from which side.
    PREFERENCE = "preference"
    # A new game that inherits colour and level from the previous one.
    REMATCH = "rematch"
    # A coaching question, or switching the trainer on or off.
    TRAINING = "training"
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


class RematchColor(StrEnum):
    """Which side the next game is played from, relative to the previous one."""

    SAME = "same"
    SWAP = "swap"
    WHITE = "white"
    BLACK = "black"


@dataclass(frozen=True, slots=True)
class PreferenceChange:
    """Only the fields the player named; everything else keeps its stored value."""

    detail_level: DetailLevel | None = None
    pause_style: PauseStyle | None = None
    notation_style: NotationStyle | None = None
    board_orientation: BoardOrientation | None = None

    def apply(self, preferences: PlayerPreferences) -> PlayerPreferences:
        return replace(
            preferences,
            detail_level=self.detail_level or preferences.detail_level,
            pause_style=self.pause_style or preferences.pause_style,
            notation_style=self.notation_style or preferences.notation_style,
            board_orientation=self.board_orientation or preferences.board_orientation,
        )


class TrainingQuestion(StrEnum):
    """What the trainer was asked; only `ENABLE` also works in an honest game."""

    ENABLE = "enable"
    DISABLE = "disable"
    # The verbal category; the number is a separate question by design.
    EVALUATION = "evaluation"
    EVALUATION_NUMBER = "evaluation_number"
    WHY_MOVE = "why_move"
    THREAT = "threat"
    CANDIDATES = "candidates"
    # «что будет, если я сыграю коня эф три» — analysed, never applied.
    PREVIEW = "preview"
    HINT = "hint"
    WHERE_WRONG = "where_wrong"
    KEEP_MOVE = "keep_move"


@dataclass(frozen=True, slots=True)
class TrainingRequest:
    question: TrainingQuestion
    # The move phrase of a `PREVIEW`, still in the words the player used.
    move_text: str | None = None


@dataclass(frozen=True, slots=True)
class RematchRequest:
    color: RematchColor = RematchColor.SAME
    # Two steps up the twenty-step scale, which is one noticeable step in play.
    harder: bool = False


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
    # What to change; set only for `PREFERENCE`.
    preference: PreferenceChange | None = None
    # How the next game differs from the previous one; set only for `REMATCH`.
    rematch: RematchRequest | None = None
    # Which coaching question was asked; set only for `TRAINING`.
    training: TrainingRequest | None = None


_CONTROL_PATTERNS: tuple[tuple[CommandKind, re.Pattern[str]], ...] = (
    (CommandKind.REPEAT_HEARD, re.compile(r"что (ты )?(услышал|поняла|понял|разобрал)|что я сказал")),
    (
        CommandKind.REPEAT_SLOW,
        re.compile(r"^повтори( еще раз)? медленн(о|ее)|^повтори (последнюю фразу|ответ)$"),
    ),
    # Help is matched before the game commands so that «справка сначала» stays
    # help navigation instead of starting a new game.
    (CommandKind.HELP_EXIT, re.compile(r"(выйти|выход|закрой|закрыть|хватит|стоп)\w*( из)? справк")),
    (
        CommandKind.HELP,
        re.compile(
            r"помощь|что ты умеешь|справка|справку|как играть|"
            r"какие команды|список команд|все команды|что можно сказать"
        ),
    ),
    (CommandKind.NEW_GAME, re.compile(r"нов(ая|ую) (игра|игру|партия|партию)|начн?ем заново|сначала|заново")),
    (CommandKind.RESIGN, re.compile(r"сдаюсь|сдаться|сдаемся|я проиграл")),
    (CommandKind.CLAIM_DRAW, re.compile(r"ничь(я|ю|ей)")),
    (CommandKind.UNDO, re.compile(r"отмен(и|ить|яю)|верн(и|уть)(?: последний)?(?: ход)?|^ход назад$|переходить")),
    (CommandKind.START, re.compile(r"начать игру|начн?ем игру|давай играть|поехали|старт")),
    (CommandKind.CONTINUE, re.compile(r"продолж")),
    (
        CommandKind.LEVEL_QUERY,
        re.compile(
            r"какой( сейчас)? уровень|какая( сейчас)? сложность|текущ(ий уровень|ая сложность)|"
            r"на каком уровне|^уровень сложности$"
        ),
    ),
    # Before the position query: «какие фигуры съедены» is a fact about the
    # game, not the «какие фигуры» listing of the current board.
    (CommandKind.GAME_FACT, game_facts.QUESTION_PATTERN),
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

# Settings are matched before the control table, so «говори медленнее» is a
# preference while «повтори медленно» stays a repeat of the previous answer.
_PREFERENCE_PATTERNS: tuple[tuple[PreferenceChange, re.Pattern[str]], ...] = (
    (
        PreferenceChange(detail_level=DetailLevel.BRIEF),
        re.compile(r"говори кратк|отвечай кратк|покороче|кратк(ие|о) ответ|краткост"),
    ),
    # Before the detailed style, whose «подробность» it also contains.
    (
        PreferenceChange(detail_level=DetailLevel.NORMAL),
        re.compile(r"обычн\w* (подробност|ответ|детальност)"),
    ),
    (
        PreferenceChange(detail_level=DetailLevel.DETAILED),
        re.compile(r"говори подробн|отвечай подробн|подробнее|подробн(ые|о) ответ|подробност"),
    ),
    (
        PreferenceChange(pause_style=PauseStyle.EXTENDED),
        re.compile(r"говори медленн|добав(ь|ляй) пауз|делай пауз|с паузами|читай медленн"),
    ),
    (
        PreferenceChange(pause_style=PauseStyle.NORMAL),
        re.compile(r"говори быстр|убери пауз|без пауз|читай быстр"),
    ),
    (
        PreferenceChange(notation_style=NotationStyle.SHORT),
        re.compile(r"коротк(ая|ую|ой) нотаци|только (клетку|поле) назначения|называй только (клетку|поле|куда)"),
    ),
    (
        PreferenceChange(notation_style=NotationStyle.FULL),
        re.compile(r"полн(ая|ую|ой) нотаци|обе клетки|называй обе"),
    ),
    # Adjacency keeps a question about the board («что на доске у черных») out of
    # the orientation setting.
    (
        PreferenceChange(board_orientation=BoardOrientation.PLAYER),
        re.compile(r"(доск\w*|ориентаци\w*) (всегда )?(как я играю|мо(им|ему) цвет\w*|по (моему )?цвету)"),
    ),
    (
        PreferenceChange(board_orientation=BoardOrientation.WHITE),
        re.compile(r"(доск\w*|ориентаци\w*) (всегда )?(за |со стороны )?бел\w+|бел\w+ снизу"),
    ),
    (
        PreferenceChange(board_orientation=BoardOrientation.BLACK),
        re.compile(r"(доск\w*|ориентаци\w*) (всегда )?(за |со стороны )?черн\w+|черн\w+ снизу"),
    ),
)

# Coaching phrases are read before the control table: «где я ошибся» and «как
# оценивается позиция» would otherwise be heard as position questions.
_TRAINING_PATTERNS: tuple[tuple[TrainingQuestion, re.Pattern[str]], ...] = (
    (
        TrainingQuestion.ENABLE,
        re.compile(r"(включи|запусти|давай)\w*( режим)? тренер|режим тренера|будь тренером|тренируй"),
    ),
    (
        TrainingQuestion.DISABLE,
        re.compile(r"(выключи|отключи|убери)\w*( режим)? тренер|без подсказок|играй честно"),
    ),
    (TrainingQuestion.KEEP_MOVE, re.compile(r"оставить мой ход|оставь мой ход|оставляю ход")),
    (TrainingQuestion.WHERE_WRONG, re.compile(r"где я ошиб|в чем моя ошибка|где была ошибка")),
    # Before the plain evaluation: the number is asked for separately.
    (
        TrainingQuestion.EVALUATION_NUMBER,
        re.compile(r"оценк\w* числ|назови оценку|сколько (сейчас )?оценка|числовая оценка"),
    ),
    (
        TrainingQuestion.EVALUATION,
        re.compile(r"как оценива|оцени позици|какая оценка|кто (сейчас )?лучше стоит|у кого (сейчас )?лучше"),
    ),
    (TrainingQuestion.WHY_MOVE, re.compile(r"почему ты (так )?(сходила|пошла|ходила)|зачем ты (так )?(сходила|пошла)")),
    (TrainingQuestion.THREAT, re.compile(r"чем ты угрожа|какая угроза|есть ли угроза|что ты задумала")),
    (TrainingQuestion.PREVIEW, re.compile(r"что будет,? если|что если я|стоит ли (мне )?(играть|ходить)")),
    (TrainingQuestion.CANDIDATES, re.compile(r"хорошие ходы|какие ходы|что мне сыграть|как мне (лучше )?сыграть")),
    (TrainingQuestion.HINT, re.compile(r"подсказ|дай совет|посоветуй|помоги с ходом")),
)

# What is left of a preview question once the framing words are dropped.
_PREVIEW_PREFIX = re.compile(
    r"^.*?(?:если (?:я )?(?:сыграю|пойду|походу|сделаю ход)?|стоит ли (?:мне )?(?:играть|ходить))\s*"
)

_REMATCH = re.compile(r"реванш|еще (одну )?(партию|игру)|сыграем еще|сыграем сложнее|сложнее|потруднее|усложни")
_REMATCH_SWAP = re.compile(r"друг(им|ой) цвет|смен(и|им|ить) цвет|поменя\w* цвет|другой стороной")
_REMATCH_HARDER = re.compile(r"сложнее|потруднее|усложни|уровень выше|посильнее|потяжелее")
_REMATCH_WHITE = re.compile(r"\bбел(ыми|ые)\b")
_REMATCH_BLACK = re.compile(r"\bчерн(ыми|ые)\b")

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

    preference = parse_preference(normalized.text)
    if preference is not None:
        return RoutedCommand(CommandKind.PREFERENCE, normalized, preference=preference, clarification=None)
    rematch = parse_rematch(normalized.text)
    if rematch is not None:
        return RoutedCommand(CommandKind.REMATCH, normalized, rematch=rematch, clarification=None)
    training = parse_training(normalized.text)
    if training is not None:
        return RoutedCommand(CommandKind.TRAINING, normalized, training=training, clarification=None)

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


def parse_preference(text: str) -> PreferenceChange | None:
    """Read a settings command, or return `None` when the phrase is not one."""
    for change, pattern in _PREFERENCE_PATTERNS:
        if pattern.search(text):
            return change
    return None


def parse_training(text: str) -> TrainingRequest | None:
    """Read a coaching question, or return `None` when the phrase is not one."""
    for question, pattern in _TRAINING_PATTERNS:
        if pattern.search(text):
            if question is not TrainingQuestion.PREVIEW:
                return TrainingRequest(question)
            # The move itself is resolved later, against the real position.
            return TrainingRequest(question, move_text=_PREVIEW_PREFIX.sub("", text).strip() or None)
    return None


def parse_rematch(text: str) -> RematchRequest | None:
    """Read a request for another game, including the colour and level it asks for."""
    if not _REMATCH.search(text):
        return None
    if _REMATCH_WHITE.search(text):
        color = RematchColor.WHITE
    elif _REMATCH_BLACK.search(text):
        color = RematchColor.BLACK
    elif _REMATCH_SWAP.search(text):
        color = RematchColor.SWAP
    else:
        color = RematchColor.SAME
    return RematchRequest(color=color, harder=bool(_REMATCH_HARDER.search(text)))


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
