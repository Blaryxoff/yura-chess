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
from yura_chess.presentation.help_speech import is_rules_request
from yura_chess.presentation.position_speech import LAST_MOVE, NUMBERED_MOVE, RANK_LINE, WHOLE_BOARD_ONLY
from yura_chess.voice.illegal_move import Explanation, IllegalReason, explain
from yura_chess.voice.move_resolver import promotion_choice, resolve
from yura_chess.voice.normalizer import MAX_UTTERANCE_LENGTH, normalize
from yura_chess.voice.types import MoveResolution, Normalized, ResolutionStatus, TokenKind

DEFAULT_CONFIDENCE_THRESHOLD = 0.7


class CommandKind(StrEnum):
    START = "start"
    NEW_GAME = "new_game"
    CONTINUE = "continue"
    RESIGN = "resign"
    CLAIM_DRAW = "claim_draw"
    UNDO = "undo"
    LEVEL_QUERY = "level_query"
    # Naming a difficulty, or asking how it is chosen, as opposed to asking which one is set.
    LEVEL = "level"
    # A question about the game itself: colour, move number, captures, castling.
    GAME_FACT = "game_fact"
    POSITION_QUERY = "position_query"
    # «что ты услышал» — replays the previous normalised utterance.
    REPEAT_HEARD = "repeat_heard"
    REPEAT_SLOW = "repeat_slow"
    REPEAT_REPLY = "repeat_reply"
    HELP = "help"
    HELP_EXIT = "help_exit"
    EXIT = "exit"
    # Leaving asked in passing rather than commanded; answered with a question.
    EXIT_CONFIRM = "exit_confirm"
    PLATFORM = "platform"
    ATTENTION = "attention"
    SOCIAL = "social"
    BACKCHANNEL = "backchannel"
    PAUSE = "pause"
    AMBIGUOUS_TURN = "ambiguous_turn"
    ORIENTATION_QUERY = "orientation"
    NAVIGATE_BACK = "navigate_back"
    WHY = "why"
    DONT_KNOW = "dont_know"
    BOARD_SETUP = "board_setup"
    SCREEN = "screen"
    # A question about who Yura is, where he went, or why he sounds like this.
    PERSONA = "persona"
    # A durable presentation setting: how much is said, how, and from which side.
    PREFERENCE = "preference"
    # A new game that inherits colour and level from the previous one.
    REMATCH = "rematch"
    # Naming the colour to play, as opposed to asking which colour is being played.
    COLOR_CHOICE = "color_choice"
    # A coaching question, or switching the trainer on or off.
    TRAINING = "training"
    # A question about a finished game: review, PGN or dictation.
    REVIEW = "review"
    # A tactical puzzle: choosing one, solving it, or leaving them.
    PUZZLE = "puzzle"
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
    sounds_enabled: bool | None = None

    def apply(self, preferences: PlayerPreferences) -> PlayerPreferences:
        return replace(
            preferences,
            detail_level=self.detail_level or preferences.detail_level,
            pause_style=self.pause_style or preferences.pause_style,
            notation_style=self.notation_style or preferences.notation_style,
            board_orientation=self.board_orientation or preferences.board_orientation,
            sounds_enabled=(self.sounds_enabled if self.sounds_enabled is not None else preferences.sounds_enabled),
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


class ReviewQuestion(StrEnum):
    """What was asked about a finished game; every answer is read-only."""

    SUMMARY = "summary"
    # Picks up an interrupted review where its stored cursor stopped.
    CONTINUE = "continue"
    TURNING_POINT = "turning_point"
    MAIN_MISTAKE = "main_mistake"
    MISTAKE_COUNT = "mistake_count"
    MOVES = "moves"
    PGN = "pgn"
    # A training branch from the turning point; started only after a confirmation.
    REPLAY_POSITION = "replay_position"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    question: ReviewQuestion


class PuzzleQuestion(StrEnum):
    """What was asked about tactical puzzles; a game is never touched by any of them."""

    START = "start"
    NEXT = "next"
    SOLUTION = "solution"
    STREAK = "streak"
    HISTORY = "history"
    REPEAT = "repeat"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class PuzzleRequest:
    question: PuzzleQuestion
    # A Lichess theme the player named, e.g. `mateIn1`; `None` picks by difficulty.
    theme: str | None = None


class ScreenWish(StrEnum):
    """What a request about the picture actually wants."""

    # «увеличь доску», «на полный экран я не вижу».
    BIGGER = "bigger"
    # «можно играть не голосом а визуально».
    TAP = "tap"


@dataclass(frozen=True, slots=True)
class ScreenRequest:
    wish: ScreenWish


class PersonaWish(StrEnum):
    """What a question about Yura is actually asking."""

    # «ты кто», «ты не алиса ты юрий», «скажи юре что он проиграл».
    WHO = "who"
    # «где юра», «куда делся юра», «а юра здесь».
    PRESENCE = "presence"
    # «почему юра со мной не разговаривает», «что он молчит».
    SILENCE = "silence"
    # «почему у тебя мужской голос», «поменяй голос на девушку».
    VOICE = "voice"


@dataclass(frozen=True, slots=True)
class PersonaRequest:
    wish: PersonaWish


class LevelIntent(StrEnum):
    """What was asked about difficulty; only `SET` may change a running game."""

    SET = "set"
    # «как поменять уровень», «снизь уровень» — a request with no number in it.
    CAPABILITY = "capability"
    # «пятнадцатый уровень это высокий или низкий» — asks what the number means.
    SCALE = "scale"


@dataclass(frozen=True, slots=True)
class LevelRequest:
    intent: LevelIntent
    # Set only for `SET`, already clamped to the engine's scale.
    level: int | None = None


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
    # What «что ты услышал» answers with, i.e. the previous turn's utterance.
    heard: str | None = None
    # Why the described move cannot be played; set only for `ILLEGAL_MOVE`.
    explanation: Explanation | None = None
    # What to change; set only for `PREFERENCE`.
    preference: PreferenceChange | None = None
    # How the next game differs from the previous one; set only for `REMATCH`.
    rematch: RematchRequest | None = None
    # Which coaching question was asked; set only for `TRAINING`.
    training: TrainingRequest | None = None
    # Which question about a finished game was asked; set only for `REVIEW`.
    review: ReviewRequest | None = None
    # Which question about Yura was asked; set only for `PERSONA`.
    persona: PersonaRequest | None = None
    # Which puzzle question was asked; set only for `PUZZLE`.
    puzzle: PuzzleRequest | None = None
    # Which difficulty was named or asked about; set only for `LEVEL`.
    level: LevelRequest | None = None
    screen: ScreenRequest | None = None
    # Number of complete player+engine turns requested by an undo command.
    undo_count: int = 1
    # The phrase without the name it was addressed by, for parsers that read it again.
    addressed: str | None = None


# Help is matched before everything else: «справка по задачам» names a help
# section, and must not open a puzzle, and «справка сначала» must not start a
# new game.
_PUZZLE_HELP_QUERY = re.compile(
    r"^(?:"
    r"какие(?: у тебя)? (?:(?:вообще )?(?:есть|бывают) )?(?:шахматн\w* )?(?:задач|головоломк)\w*"
    r"(?: у тебя (?:есть|бывают)| доступны| ты (?:можешь|умеешь) (?:дать|предложить))?|"
    r"(?:у тебя )?какие(?: есть| бывают)? (?:шахматн\w* )?(?:задач|головоломк)\w*|"
    r"(?:есть )?какие(?: нибудь)? (?:шахматн\w* )?(?:задач|головоломк)\w*(?: есть)?|"
    r"(?:какие(?: у тебя)? (?:есть )?)?(?:виды|типы|тема|темы|категории) "
    r"(?:шахматн\w* )?(?:задач|головоломк)\w*(?: есть| доступны| у тебя есть)?|"
    r"(?:на какие темы (?:у тебя )?есть (?:шахматн\w* )?(?:задач|головоломк)\w*|"
    r"по каким темам можно порешать)|"
    r"(?:расскажи|объясни)(?: мне)? (?:про|о) (?:шахматн\w* )?"
    r"(?:задачи|задачах|головоломки|головоломках)|"
    r"что за (?:шахматн\w* )?(?:задачи|головоломки)(?: у тебя есть)?|"
    r"что (?:у тебя )?есть из (?:задач|головоломок)|что можно порешать"
    r")$"
)

_HELP_PATTERNS: tuple[tuple[CommandKind, re.Pattern[str]], ...] = (
    (CommandKind.HELP_EXIT, re.compile(r"(выйти|выход|закрой|закрыть|хватит|стоп)\w*( из)? справк")),
    (CommandKind.HELP, _PUZZLE_HELP_QUERY),
    (
        CommandKind.HELP,
        re.compile(
            # «помоги» alone asks for the help section; «помоги с ходом» asks the
            # trainer for one move and is read as a hint further down.
            r"помощь(?: пожалуйста)?$|помощь (по|с|про)\b|помощь нужна$|"
            r"^(?:(?:алиса|юра|пожалуйста|прошу|просим|попросить|попросите|нужна|нужно)\s+)*"
            r"помощ[иь](?:\s+(?:пожалуйста|нужна))*$|"
            r"^(?:(?:алиса|юра|пожалуйста)\s+)*помоги(?:те)?(?:\s+(?:мне|пожалуйста|помогите))*$|"
            r"что ты (умеешь|можешь делать)|справка|справку|справке|как играть|"
            r"что еще ты умеешь|что ты можешь$|расскажи (?:мне )?о возможност\w*|"
            r"какие команды|список команд|все команды|что можно сказать|какие еще (есть )?опции|^настройки$|"
            r"^что (?:мне )?делать$|как (?:с тобой )?играть|как (?:сделать|назвать) ход"
        ),
    ),
)

# The skill is named after a man who never introduces himself, so players look for
# him: they ask where he went, why he is silent, and who is answering instead.
_PERSONA_VOICE = re.compile(
    r"(?:почему|отчего)\b(?:\s+\w+){0,2}\s+(?:у тебя|у вас|твой|ваш)\b(?:\s+\w+){0,3}\s+голос\w*|"
    r"(?:ты|вы)\b(?:\s+\w+){0,2}\s+(?:мужск|женск)\w+\s+голос\w*|"
    r"(?:помен|измен|смен)\w+\s+(?:\w+\s+){0,2}голос|"
    r"нужен (?:мужской|женский)\w*\s*(?:не \w+\s*)?голос|"
    r"^вы девушк\w+ или (?:мальчик|мужчин)\w*$|куда делась алиса девушка"
)
_PERSONA_PRESENCE = re.compile(
    r"^(?:а |ну )?(?:где|куда)\b(?:\s+(?:сейчас|же|там|тут|дел[ася]+|пропал\w*))*\s+юр\w+$|"
    r"^(?:а |ну )?юр(?:а|ий) (?:здесь|тут)$|^без юры$"
)
_PERSONA_SILENCE = re.compile(
    r"юр\w*(?:\s+\w+){0,3}\s+(?:не разговарива|молчи|не отвеча)\w*|"
    r"почему\b(?:\s+\w+){0,3}\s+юр\w*(?:\s+\w+){0,3}\s+(?:не разговарива|молчи|не отвеча)\w*"
)
_PERSONA_WHO = re.compile(
    r"^(?:а |эй |ну )?(?:ты|вы) кто(?: такой| такая| ты)?$|^(?:а |ну )?кто ты(?: такой| такая)?$|"
    r"\bмужик ты кто\b|ты кто такой юр\w+ или алис\w+|"
    r"ты не (?:алиса|юра|юрий) ты (?:юрий|юра|алиса)|"
    r"^(?:а )?(?:он|ты) робот\??$|^ты живой\??$|"
    r"^вызови юру$|^кто такой юра$"
)
_PERSONA_REQUEST = re.compile(
    f"{_PERSONA_VOICE.pattern}|{_PERSONA_PRESENCE.pattern}|{_PERSONA_SILENCE.pattern}|{_PERSONA_WHO.pattern}"
)


def parse_persona(text: str) -> PersonaRequest | None:
    """Read a question about Yura, or `None` when the phrase is not one."""
    if _PERSONA_VOICE.search(text):
        return PersonaRequest(PersonaWish.VOICE)
    if _PERSONA_SILENCE.search(text):
        return PersonaRequest(PersonaWish.SILENCE)
    if _PERSONA_PRESENCE.search(text):
        return PersonaRequest(PersonaWish.PRESENCE)
    if _PERSONA_WHO.search(text):
        return PersonaRequest(PersonaWish.WHO)
    return None


# Alice owns the device. The skill holds the microphone but not the volume, the
# television or the music, so the only answer that works is to hand the session back.
_PLATFORM_MEDIA = (
    r"^(?:алиса |алис )?(?:(?:включи|поставь|запусти|выключи|останови|продолжи)\w*"
    r"(?:\s+\w+){0,3}\s+"
    r"(?:музык|песн|трек|радио|сказк|новост)\w*(?: .*)?|"
    r"(?:музык|песн|трек|радио|новост)\w*(?: .*)? (?:включи|поставь|запусти)\w*|"
    r"расскажи (?:сказк|истори)\w*(?: .*)?|"
    r"(?:какая|расскажи|покажи|скажи)? ?"
    r"(?:погод\w*|прогноз погод\w*)(?: .*)?)$"
)
# «говори громче» belongs to the speech settings, not here: only a device-volume
# phrasing hands the session back, so its siblings «говори быстрее» keep working.
_PLATFORM_VOLUME = (
    r"^(?:алиса |алис )?громкост\w*(?: на)? \d+\b|(?:прибав|убав|сделай|поставь|измени)\w*\s+громкост|"
    r"(?:сделай|поставь)\w*(?:\s+\w+){0,2}\s+звук на\b|"
    r"(?:сделай|включи|поставь|прибав|убав)\w*(?:\s+\w+){0,2}\s+(?:по)?(?:громче|тише)\b|"
    r"(?:прибав|убав)\w*\s+звук\b|звук\s+(?:по)?(?:громче|тише)\b|"
    r"^(?:по)?(?:громче|тише)(?: пожалуйста)?$"
)
_PLATFORM_DEVICE = (
    r"(?:выключи|включи|отключи|зажги|погаси)\w*(?:\s+\w+){0,3}\s+(?:телевизор\w*|телек\w*|свет\b|патефон\w*|колонк\w*)"
)
_PLATFORM_ELSEWHERE = (
    r"каки\w+ игры у тебя есть|во что нибудь друг\w+ (?:поигра|сыгра)\w*|"
    # «может в другую игру» leaves chess; «сыграем другую партию» is a rematch.
    r"може\w*(?:\s+\w+){0,2}\s+друг\w+ игр\w*|в друг\w+ шашк\w*|"
    r"^(?:а |ну |давай |алиса )*погово[рм]\w*(?: с тобой)?$|поболта\w+|о чем\b.*поговорим"
)
_PLATFORM_REQUEST = re.compile(f"{_PLATFORM_MEDIA}|{_PLATFORM_VOLUME}|{_PLATFORM_DEVICE}|{_PLATFORM_ELSEWHERE}")


_CONVERSATION_PATTERNS: tuple[tuple[CommandKind, re.Pattern[str]], ...] = (
    # Before the platform handoff: «алиса измени голос» is about Yura, not about Alice.
    (CommandKind.PERSONA, _PERSONA_REQUEST),
    (CommandKind.PLATFORM, _PLATFORM_REQUEST),
    (CommandKind.ATTENTION, re.compile(r"^(?:алиса|алис|юра)$")),
    (
        CommandKind.SOCIAL,
        re.compile(
            r"^(?:привет|здравствуй|здравствуйте|доброе утро|добрый день|добрый вечер|"
            r"ты тут|ты здесь|как тебя зовут|как дела|как ты|как поживаешь|"
            r"(?:огромное |большое )?спасибо(?: (?:большое|огромное|тебе|вам|за (?:игру|партию|урок)))?)$"
        ),
    ),
    (
        CommandKind.BACKCHANNEL,
        re.compile(r"^(?:понятно|понял|поняла|хорошо|ладно|нормально|угу|поехали|погнали|вперед|начали|начинаем)$"),
    ),
    (CommandKind.REPEAT_REPLY, re.compile(r"^(?:повтори|еще раз)$")),
    (
        CommandKind.PAUSE,
        re.compile(
            r"^(?:подожди|погоди|пауза|секунду|минуту)(?: пожалуйста)?$|"
            r"^я еще (?:поле|доску|фигуры) выставляю$"
        ),
    ),
    (CommandKind.AMBIGUOUS_TURN, re.compile(r"^ходы?$")),
    (CommandKind.ORIENTATION_QUERY, re.compile(r"^(?:разверни|поверни) доску$")),
    (CommandKind.NAVIGATE_BACK, re.compile(r"^вернись$")),
    (
        CommandKind.CANCEL_CLARIFY,
        re.compile(r"^(?:отмена|я передумал[а]?|я не это имел[а]? в виду|не это)$"),
    ),
    (CommandKind.WHY, re.compile(r"^почему$")),
    (CommandKind.DONT_KNOW, re.compile(r"^(?:не знаю|не помню)$")),
)

_COLOR_VERB = r"(?:игра(?:ю|ть|ем|л[аи]?)|сыгра(?:ю|ем|ть)|бу(?:ду|дем)|хочу|хотел\w*|давай(?:те)?|можно|дай|дайте)"
# Only the words that can stand between «играю» and the colour without changing
# what is being asked. Any word at all would swallow «можно ходить только
# белыми фигурами», which asks about the rules and starts nothing.
_COLOR_FILLER = (
    r"(?:\s+(?:я|мне|мы|нам|бы|же|за|сейчас|теперь|тогда|снова|опять|лучше|пожалуйста|эту|эта|партию|партия|игру))*"
)
# The request has to end at the colour: «можно белыми сделать рокировку» and
# «можно черными брать на проходе» ask what that side may do, not to play it.
_COLOR_TAIL = r"(?:\s+(?:фигурами|фигуры|цветом|эту|эта|партию|партия|игру|сейчас|теперь|пожалуйста))*\s*$"
_COLOR_CHOICE = re.compile(
    rf"\b{_COLOR_VERB}\b{_COLOR_FILLER}\s+(?P<color>бел|черн)(?:ыми|ых)\b{_COLOR_TAIL}"
    rf"|\b(?P<second>бел|черн)ыми\b{_COLOR_FILLER}\s+(?:игра|сыгра)\w*{_COLOR_TAIL}"
)
# «как лучше играть белыми» asks for advice on a side already being played.
_COLOR_ADVICE = re.compile(r"^как\b|\bкак (?:лучше|правильно)\b")
_COLOR_ALTERNATIVE = re.compile(r"\b(?:бел|черн)\w*\s+(?:или|либо)\s+(?:бел|черн)")
# «ты будешь играть черными» hands the colour to the engine, so reading it as the
# player's would deal the opposite of what was asked. The names are deliberately
# not here: «юра, давай белыми» addresses the skill, it does not deal it a colour.
_COLOR_FOR_ENGINE = re.compile(r"\b(?:ты|тебе|тобой|тво(?:й|я|им))\b|\bчтобы\s+(?:юр\w*|алис\w*)\b")
_COLOR_REFUSAL = re.compile(r"\bне\s+(?:буду|будем|хочу|хотел\w*|игра\w*|сыгра\w*|давай\w*)")
# After a refusal only a fresh request re-opens the choice: «не хочу белыми давай
# черными» asks for black, while «не буду играть черными» asks for nothing.
_COLOR_RETRY = re.compile(
    rf"\b(?:давай(?:те)?|хочу|хотел\w*|дай|дайте|бу(?:ду|дем)|сыгра(?:ю|ем))\b{_COLOR_FILLER}"
    rf"\s+(?P<color>бел|черн)(?:ыми|ых)\b"
)

# Surrender names the loser and ends the game outright; the forms below only end
# it once the game is named too, so both are read against the same utterance.
_SURRENDER = r"сдаюсь|сдаться|сдаемся|я сдался"
# Asking for the end of the game, not describing one: «кто завершил игру» and
# «почему ты закончила эту игру» ask about a game that is already over.
_END_GAME_REQUEST = (
    r"(?:зак[оа]нч(?:и|им|ите|ить|ивай(?:те)?|иваем)|заверш(?:и|им|ите|ить|ать|аем)|"
    r"прекра(?:ти|тим|тите|тить|щать|щаем))"
)
_END_GAME_STEM = r"(?:зак[оа]нч|заверш|прекра[тщ])"

MAX_LEVEL = 20

# Cardinal and ordinal stems of one number, longest first: «пят» must not answer for «пятнадцатый».
_LEVEL_NUMBER_STEMS_LONGEST_FIRST: tuple[tuple[str, int], ...] = (
    ("одиннадцат", 11),
    ("двенадцат", 12),
    ("тринадцат", 13),
    ("четырнадцат", 14),
    ("пятнадцат", 15),
    ("шестнадцат", 16),
    ("семнадцат", 17),
    ("восемнадцат", 18),
    ("девятнадцат", 19),
    ("двадцат", 20),
    ("тридцат", 30),
    ("сорок", 40),
    ("пятьдесят", 50),
    ("пятидесят", 50),
    ("шестьдесят", 60),
    ("шестидесят", 60),
    ("семьдесят", 70),
    ("семидесят", 70),
    ("восемьдесят", 80),
    ("восьмидесят", 80),
    ("девяност", 90),
    ("четверт", 4),
    ("четыре", 4),
    ("нул", 0),
    ("ноль", 0),
    ("перв", 1),
    ("один", 1),
    ("втор", 2),
    ("два", 2),
    ("две", 2),
    ("трет", 3),
    ("три", 3),
    ("пят", 5),
    ("шест", 6),
    ("седьм", 7),
    ("сем", 7),
    ("восьм", 8),
    ("восем", 8),
    ("девят", 9),
    ("десят", 10),
)
_LEVEL_NUMBER = "|".join(rf"{stem}\w*" for stem, _ in _LEVEL_NUMBER_STEMS_LONGEST_FIRST)
_LEVEL_NOUN = r"уровн\w*|уровен\w*|сложност\w*"
# The number leads or follows: «уровень номер ноль», «нулевой уровень», «1 уровень», «уровень на пять».
_LEVEL_VALUE_AFTER = re.compile(
    rf"(?:{_LEVEL_NOUN})\s+(?:сложности\s+|игры\s+)?(?:номер\s+)?(?:на\s+)?(?P<value>\d{{1,3}}|{_LEVEL_NUMBER})\b"
)
_LEVEL_VALUE_BEFORE = re.compile(rf"\b(?P<value>\d{{1,3}}|{_LEVEL_NUMBER})\s+(?:{_LEVEL_NOUN})")
_LEVEL_COMMAND = re.compile(rf"\b(?:{_LEVEL_NOUN})\b")
_LEVEL_SCALE = re.compile(
    r"(?:высок\w*|низк\w*|сильн\w*|слаб\w*|больш\w*|маленьк\w*|много|мало)\s+или\s+|"
    r"это\s+(?:высок|низк|сильн|слаб|много|мало)|"
    r"сколько\s+(?:всего\s+|у тебя\s+)?уровн|"
    r"каки\w*\s+(?:бывают|есть|вообще)\s+уровн|"
    r"(?:максимальн\w*|минимальн\w*|сам\w+\s+(?:высок|сильн|сложн|легк|прост)\w*)\s+уровен|"
    r"что\s+(?:значит|означает|значат)\s+уровен"
)
# «игра белыми уровень пять» asks for a game at that level, not for a change to the running one.
_LEVEL_STARTS_A_GAME = re.compile(
    r"\b(?:бел|черн)(?:ыми|ых|ые)\b|\b(?:игр|сыгр|поигр)(?:ать|аем|аю|аешь|ай|айте)\b|"
    r"\b(?:игра|сыгра)\w*\s+(?:парти|игр)\w*|\b(?:парти|игр)\w*\s+(?:игра|сыгра)\w*"
)
# Everything a bare setter may carry besides the level itself; anything else makes the phrase a question.
_LEVEL_SETTER_WORDS = frozenset(
    "а ну юра юр алиса пожалуйста прошу спасибо окей ок хорошо ладно давай давайте давай-ка "
    "теперь сейчас тогда пусть будет вот на себе "
    "можно можешь можете могу мне я ты хочу хотел хотела хотелось бы надо нужно нужен "
    "поставь поставьте поставить поставим ставь ставить ставим "
    "установи установите установить установим сделай сделайте сделать сделаем "
    "поменяй поменяйте поменять поменяем смени смените сменить сменим "
    "измени измените изменить изменим переключи переключить переключись переключимся "
    "задай задать выстави выставить включи дай дайте".split()
)
# «уровень громкости» belongs to Alice, not to the chess engine.
_LEVEL_NOT_CHESS = re.compile(r"\b(?:громкост|звук|сигнал|батаре|заряд|яркост|шум)\w*")
_VOCATIVES = frozenset({"алиса", "алис", "юра", "юрий", "юр"})
# Words that only ever open an address: «ну юра, твой ход».
_ADDRESS_FILLERS = frozenset({"ну", "а", "все", "ладно", "эй"})
_LEVEL_KINDS = frozenset({CommandKind.LEVEL, CommandKind.LEVEL_QUERY})
_LEAVING_KINDS = frozenset({CommandKind.EXIT, CommandKind.EXIT_CONFIRM, CommandKind.PLATFORM})
# «как выйти из партии победителем» asks how a game is won, not to be let out.
_LEAVING_ASKED_AS_A_RULE = re.compile(
    r"\bкак\b(?:\s+\w+){0,2}\s+(?:выйти|выход|выйду)\s+из\s+(?:парти|игр|позици)\w*\s+\w+"
)
# «а уровень пять как сделать» asks how, and outranks the number it happens to carry.
_LEVEL_HOW = re.compile(
    r"\bкак\b(?:\s+[а-я]+){0,4}?\s+"
    r"(?:выбрать|выбираешь|поменять|изменить|сменить|поставить|установить|настроить|задать|сделать|"
    r"повысить|понизить|снизить|уменьшить|увеличить)"
)


def _spoken_level(match: re.Match[str]) -> int | None:
    spoken = match.group("value")
    if spoken.isdigit():
        return max(0, min(int(spoken), MAX_LEVEL))
    for stem, value in _LEVEL_NUMBER_STEMS_LONGEST_FIRST:
        if spoken.startswith(stem):
            return min(value, MAX_LEVEL)
    return None


def parse_level_value(text: str) -> int | None:
    """Read the difficulty a phrase names, in any of the forms it is spoken in."""
    match = _LEVEL_VALUE_AFTER.search(text) or _LEVEL_VALUE_BEFORE.search(text)
    return _spoken_level(match) if match is not None else None


def parse_level(text: str) -> LevelRequest | None:
    """Read a difficulty command, or return `None` when the phrase is not one.

    A number only sets the level when nothing but polite or imperative words
    surrounds it: «что значит пятый уровень» asks, and may never change a game.
    """
    if not _LEVEL_COMMAND.search(text) or _LEVEL_STARTS_A_GAME.search(text) or _LEVEL_NOT_CHESS.search(text):
        return None
    if _LEVEL_SCALE.search(text):
        return LevelRequest(LevelIntent.SCALE)
    if _LEVEL_HOW.search(text):
        return LevelRequest(LevelIntent.CAPABILITY)
    match = _LEVEL_VALUE_AFTER.search(text) or _LEVEL_VALUE_BEFORE.search(text)
    if match is None:
        return LevelRequest(LevelIntent.CAPABILITY)
    value = _spoken_level(match)
    remainder = f"{text[: match.start()]} {text[match.end() :]}".split()
    if value is None or not all(word in _LEVEL_SETTER_WORDS for word in remainder):
        return LevelRequest(LevelIntent.SCALE)
    return LevelRequest(LevelIntent.SET, value)


# Two different needs, and the skill grants neither: the card is drawn to Alice's
# own shape, and a move can only be spoken.
_SCREEN_CANNOT_SEE = re.compile(
    r"увелич(?:ь|ьте|ить|им)\b\s+(?:\w+\s+)?(?:доск|картинк|экран)|"
    r"(?:сделай|поставь)\s+(?:доску|картинку|экран)\s+больше|"
    r"(?:больше|крупнее|побольше)\s+(?:доск|картинк|экран)|"
    r"(?:на|во)\s+весь\s+экран|на полный экран|"
    r"(?:не вижу|плохо вижу|не видно|вижу плохо)\s+(?:\w+\s+)?(?:доск|картинк|экран|символ|букв[ыуаео]?\b)|"
    r"^увелич\w*\s+(?:вижу плохо|плохо вижу)$|"
    r"маленьк\w+\s+(?:символ|букв|доск|картинк)"
)
_SCREEN_WANTS_TO_TAP = re.compile(
    r"игра(?:ть|ем)?\s+(?:не голосом|визуально)|только визуально|визуально\s+постав\w*|"
    r"не голосовым|нажим\w*\s+на клетк"
)
_SCREEN_REQUEST = re.compile(f"{_SCREEN_CANNOT_SEE.pattern}|{_SCREEN_WANTS_TO_TAP.pattern}")


def parse_screen(text: str) -> ScreenRequest | None:
    """Read a request about the picture, or `None` when the phrase is not one."""
    if _SCREEN_WANTS_TO_TAP.search(text):
        return ScreenRequest(ScreenWish.TAP)
    if _SCREEN_CANNOT_SEE.search(text):
        return ScreenRequest(ScreenWish.BIGGER)
    return None


_CONTROL_PATTERNS: tuple[tuple[CommandKind, re.Pattern[str]], ...] = (
    (CommandKind.REPEAT_HEARD, re.compile(r"что (ты )?(услышал[аи]?|понял[аи]?|разобрал[аи]?)|что я сказал")),
    (
        CommandKind.REPEAT_SLOW,
        re.compile(r"^повтори( еще раз)? медленн(о|ее)|^повтори (последнюю фразу|ответ)$"),
    ),
    (
        CommandKind.NEW_GAME,
        re.compile(
            r"нов(ая|ую) (игра|игру|партия|партию)|начн?ем заново|сначала|заново|"
            r"(?:хочу |буду )?(?:сыграть|играть) за (бел|черн)|"
            r"можно (?:мне )?за (бел|черн)"
        ),
    ),
    (
        CommandKind.RESIGN,
        re.compile(
            rf"{_SURRENDER}|"
            # Naming the game ends the game; naming the skill is the exit above.
            # An adjective may sit between: «заканчиваем шахматную партию». A
            # negation cancels it outright, and is checked by _END_GAME_NEGATED.
            rf"\b{_END_GAME_REQUEST}\b(?: [а-я]+)? (?:игру|партию)\b|"
            r"^(?:алиса |алис |юра )?стоп (?:игра|игру|партия|партию)$|"
            r"^(?:алиса |алис )?(?:давай )?закончим этот тур$|"
            # Only the whole utterance: «конец игры это мат или пат» names the term.
            r"^(?:алиса |юра )?конец (?:игры|партии)$|"
            r"игра окончена|^(?:игра|партия) (?:окончена|закончена|завершена)$"
        ),
    ),
    (CommandKind.CLAIM_DRAW, re.compile(r"ничь(я|ю|ей)")),
    (
        CommandKind.UNDO,
        re.compile(
            r"\b(?:отмен(?:и|ить)|откат(?:и|ить)|верн(?:и|уть))\b(?: [а-я0-9]+){0,3} ход\w*\b|"
            # What ASR makes of «вернуть ход». Kept to the whole utterance: the
            # filler above would let «поверни доску чей ход» undo a move.
            r"^(?:по|вы)верн(?:и|уть) ход\w*$|"
            r"^ход назад$|переходить"
        ),
    ),
    (
        CommandKind.START,
        re.compile(
            r"начать игру|начн?ем игру|давай (?:по)?играем(?: в шахматы)?|"
            r"давай играть|давай сыграем|^хочу играть$|старт"
        ),
    ),
    (
        CommandKind.CONTINUE,
        re.compile(
            r"^(?:алиса )?(?:(?:да|ага) )?(?:давай )?"
            r"продолж(?:ай|айте|аем|и|ите|им|ить)?(?: (?:игру|партию|последнюю партию))?(?: пожалуйста)?$|"
            r"^(?:алиса )?(?:теперь )?твой ход$|^вернемся к (?:игре|партии)$"
        ),
    ),
    (
        CommandKind.EXIT,
        re.compile(
            r"^(?:алиса )?(выход|выйти|стоп|выключи|замолчи)$|выключись|"
            r"^(?:алиса |юра )?отключи(?:сь|ться)(?: |$)|"
            # A noun, never an adjective: «выключи шахматного тренера» is a trainer command.
            r"(?:выключ|отключ|выруб|убер)\w*(?:\s+\w+){0,3}\s+"
            r"(?:навык\w*|шахмат[ыуе]\b|шахматами\b|юр[уеы]\b|себя\b|меня\b|"
            r"эт\w+ игру|эт\w+ программу|эт\w+ режим|шахматн\w+ программу|программу для шахмат)|"
            r"(?:убрать шахмат|шахмат\w* убрать)|"
            # The object is required: «как выйти из шаха» asks a rule, not to leave.
            r"как\s+(?:тебя|это|отсюда)\s+(?:выключить|отключить|выйти)|"
            r"как\s+отключить\s+(?:эт\w+\s+)?(?:навык|юри|шахмат)|"
            r"^(?:а )?как\s+отключиться$|помощь как выключить|"
            r"(?:выйди|выйти|выход) из (?:шахмат|навыка|игры|партии)(?: пожалуйста)?$|"
            r"до свидания|закрой навык|"
            # Anchored: «подожди пока я думаю» is not a farewell, but a trailing doubled «пока» is.
            r"^(?:ну |все |ладно |спасибо |алиса |алис |юра )*пока(?: пока)*"
            r"(?: алиса| юра| пожалуйста)?$|\bнет пока пока$|"
            r"стоп навык|^стоп шахматы$|^(?:а )?(?:можешь )?постопить$|"
            r"(?:хватит|выключи|надо) стоп$|выключи хватит$|"
            r"законч\w+ сесси|заверш\w+ сесси|"
            r"^(?:все |алиса |давайте )?(?:выходим|выйдем)(?: выходим)?(?: из (?:игры|шахмат|навыка))?$|"
            r"надоел\w*(?:\s+\w+){0,2}\s+шахмат[ыуе]\b|"
            r"^(?:алиса |алис )?прекрати(?:те)?(?: пожалуйста)?$|"
            r"^(?:алиса |алис )?стоп пожалуйста$|ты меня достал\w*|"
            # A refusal that names a colour is choosing sides, not leaving.
            r"(?:закончить|закрой|останови) (навык|шахматы)|"
            r"не хочу (больше )?играть(?!\s+(?:бел|черн))|хватит играть|"
            r"закончим на сегодня"
        ),
    ),
    (
        CommandKind.BOARD_SETUP,
        re.compile(
            r"\b(?:до)?расстав(?:лю|им|ить|ляю|ил|ила|или)\b|принес\w* (?:свои )?шахмат|"
            r"игра\w* (?:на|с) (?:своей )?доск|подожди,? (?:я )?расстав|"
            r"ты играешь (?:за )?черн"
        ),
    ),
    (
        CommandKind.LEVEL_QUERY,
        re.compile(
            r"какой( сейчас)? уровень|какая( сейчас)? сложность|текущ(ий уровень|ая сложность)|"
            r"на каком уровне|^уровень сложности$|(?:поменять|изменить) уровень"
        ),
    ),
    # After the query, and after the start and rematch phrases that name a level of their own.
    (CommandKind.LEVEL, _LEVEL_COMMAND),
    # Before the position query: «какие фигуры съедены» is a fact about the
    # game, not the «какие фигуры» listing of the current board.
    (CommandKind.GAME_FACT, game_facts.QUESTION_PATTERN),
    # Before the position query: «покажи доску на весь экран» asks about the picture, not the pieces.
    (CommandKind.SCREEN, _SCREEN_REQUEST),
    (
        CommandKind.POSITION_QUERY,
        re.compile(
            r"кака(я|ю) позици|позици(я|ю)|расстановк|\bгде\b|что на|покажи (?:мне\s+)?(?:\w+\s+)?(?:доску|поле)\b|"
            r"какие (?:у меня )?фигуры|сколько фигур|прочитай|"
            rf"{WHOLE_BOARD_ONLY.pattern}|"
            # A rank only when one is named: «мат по последней горизонтали» is a
            # term, and «ходить по горизонтали» a rule, neither reads the board.
            rf"{RANK_LINE.pattern}|"
            # «на» has to follow: «что стоит сыграть» asks for advice, and
            # «что стоит перед королем» asks a relation the board reader cannot answer.
            r"(?:кто|что) (?:стоит|находится) на\b|"
            rf"{NUMBERED_MOVE.pattern}|"
            r"чей ход|кто ходит|кому ходить|моя очередь|есть ли шах|кто под шахом|шах сейчас|"
            rf"{LAST_MOVE.pattern}|"
            r"ход(а|ов)? назад|раз(а)? назад|повтори координат|"
            r"что (сделали|делали) (белые|черные)|назови еще раз (свой|последний) ход|"
            r"^(дальше|далее)$"
        ),
    ),
    # After the board questions: «у черных», «за белых» and «что сделали белые»
    # are the genitive, accusative and nominative those answer with. Only the
    # instrumental — the case «играю …» takes — asks to play that colour.
    (CommandKind.COLOR_CHOICE, _COLOR_CHOICE),
    # Last of the control table: leaving named loosely — «выход пожалуйста»,
    # «юра выход», «я хочу выйти». «выход коня на е пять» is a developing move,
    # never a request to leave, so a piece behind the word rules it out.
    (
        CommandKind.EXIT_CONFIRM,
        re.compile(
            r"\b(?:выход|выйти|выйду|выхожу)\b(?!\s+(?:[а-я]+\s+){0,4}(?:кон[ьяем]|слон|ферз|ладь|корол|пешк|шах(?:а|у|ом|е)?\b))"
        ),
    ),
)

_RULES_FRAME = re.compile(r"\bкак (?:с?делать|играть|ходит|пойти)\b|\bможно ли\b|\bможет ли\b|\bчто такое\b")
_INCOMPLETE_MOVE = re.compile(r"^(мой ход|я хожу|я буду ходить)$")
_MOVE_SEQUENCE = re.compile(r"\b(?:потом|затем|после этого)\b")
# Words that retract what was just said. They are read off the raw utterance
# because the normaliser drops the punctuation a correction leans on.
# Bare «нет» only after a pause, so that «в справке нет команды» keeps its
# ordinary meaning; «ой нет» is already covered by «ой».
_RETRACTION = re.compile(
    # «я не это имел в виду» cancels what is pending; it corrects nothing said
    # in the same breath, so the idiom is kept out of the markers.
    r"\b(?:ой|не так|не туда|не это)(?!\s+(?:имел|хотел|сказал)\w*)\b"
    r"|\b(?:точнее|то есть|отставить|извини\w*|прости\w*)\b|(?<=[,;])\s*нет\b"
)
_SEGMENT = re.compile(r"[,;]")
_UNDO_COUNT = re.compile(
    r"\b(?P<count>\d+|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+"
    r"(?:полных?\s+)?ход"
)
_COUNT_VALUES = {
    "один": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}

# Bare «музыка» is left out on purpose: «включи музыку» stays a platform request
# for Alice, while «музыкальное сопровождение» is this skill's own cues.
# «сигнализация» is an alarm, not a game cue, so the stem stops short of it.
_SOUND_NOUN = r"(?:звук\w*|озвучк\w*|сигнал(?!из)\w*|сопровожден\w*)"
# Up to two words between the verb and the noun: «выключи мне игровые звуки».
_SOUND_GAP = r"(?:\s+[а-я]+){0,2}?"
_SOUND_WISH = r"(?:хочу|хочется|хотел\w*|надо|нужн\w*|буд(?:у|ем)|стоит|давай\w*)"
# A wish may sit between the negation and the verb: «не хочу выключать звуки»
# asks for the opposite of «выключать звуки», not for the same thing.
_SOUND_NEGATION = rf"(?P<negated>\bне\s+(?:{_SOUND_WISH}\s+)?)?"
# A refusal to end the game, in the three shapes it is spoken in. A wish and one
# word may sit between the refusal and the verb, but only an infinitive belongs
# to it: in «не хочу сейчас, закончи игру» the imperative opens a new clause and
# is the request the utterance ends with.
_END_GAME_NEGATED = re.compile(
    rf"\bне\s+{_END_GAME_STEM}"
    rf"|\bне\s+{_SOUND_WISH}\s+(?:[а-я]+\s+)?{_END_GAME_STEM}\w*ть\b"
    rf"|{_END_GAME_STEM}\w*(?:\s+[а-я]+)? (?:игру|партию)(?:\s+[а-я]+)?\s+не\s+{_SOUND_WISH}\b"
)
# «что значит закончить партию» asks what ending a game means. Kept apart from
# _RULES_FRAME: «можно ли закончить игру» is how a game is actually resigned.
_END_GAME_DEFINITION = re.compile(r"\bчто (?:значит|такое)\b")
# «не выключай шахматы» keeps the session, and so does «не говори до свидания».
# Read off the whole utterance: a refusal anywhere in it outranks the request.
_LEAVING_NEGATED = re.compile(
    r"\bне\s+(?:\w+\s+){0,3}(?:выключ|отключ|выруб|убир|убер|закрыв|закрой|законч|заверш|говор|прибав|убав|мен[яю]|выйти|выход|надоел|постопить|прекрати)\w*"
)
_SURRENDER_SPOKEN = re.compile(_SURRENDER)
# Only a wish stands between the refusal and the word it refuses: «я не проиграл, но сдаюсь» resigns.
_SURRENDER_REFUSED = re.compile(r"\bне\s+(?:(?:хочу|хочется|хотел\w*|надо|нужн\w*|буд(?:у|ем))\s+)?$")
# A refusal and a question hold only over the clause they were spoken in, so
# both are read against the last one an utterance opens.
_LAST_CLAUSE = re.compile(r"\bа (?:теперь|потом|сейчас)\b")
_SOUND_COMMAND = re.compile(
    rf"{_SOUND_NEGATION}"
    rf"(?:(?P<on>включ|верн|добав)|выключ|отключ|убер|убир|отмен)\w*{_SOUND_GAP}\s+{_SOUND_NOUN}"
)
# «не играй со звуком» asks for silence, so the game-mode forms carry the same
# negation as the plain commands do.
_SOUND_MODE = re.compile(
    rf"{_SOUND_NEGATION}"
    rf"(?:игра(?:й|ем|ть|ю)|сыгра\w+|парти\w+|можно|давай\w*){_SOUND_GAP}"
    rf"\s+(?:(?P<on>со?)|без)\s+(?:[а-я]+\s+)?{_SOUND_NOUN}"
)
_SOUND_UNWANTED = re.compile(
    rf"\bне\s+{_SOUND_WISH}\s+(?:[а-я]+\s+)?{_SOUND_NOUN}"
    rf"|\b{_SOUND_NOUN}\s+(?:мне\s+)?не\s+{_SOUND_WISH}"
    rf"|^без\s+(?:[а-я]+\s+)?{_SOUND_NOUN}"
    r"|\bв тишине\b"
)
# «что со звуком» is a question about the device, not a request for the cues,
# so the bare form is only a setting when it is the whole utterance.
_SOUND_WANTED = re.compile(
    rf"\b{_SOUND_WISH}\s+(?:[а-я]+\s+)?{_SOUND_NOUN}"
    rf"|^со?\s+(?:[а-я]+\s+)?{_SOUND_NOUN}$"
)
# «почему ты выключила звуки» wonders about the cues; only a request may store a
# new setting, so an utterance opening with a question word never does.
_SOUND_QUESTION = re.compile(r"^(?:почему|зачем|отчего|когда|разве|неужели)\b")


def _sound_preference(text: str) -> PreferenceChange | None:
    """Read the cue switch however it is phrased; a negation flips the verb."""
    if _SOUND_QUESTION.match(text):
        return None
    for pattern in (_SOUND_COMMAND, _SOUND_MODE):
        match = pattern.search(text)
        if match is not None:
            enabling = match.group("on") is not None
            return PreferenceChange(sounds_enabled=enabling if match.group("negated") is None else not enabling)
    if _SOUND_UNWANTED.search(text):
        return PreferenceChange(sounds_enabled=False)
    if _SOUND_WANTED.search(text):
        return PreferenceChange(sounds_enabled=True)
    return None


# Settings are matched before the control table, so «говори медленнее» is a
# preference while «повтори медленно» stays a repeat of the previous answer.
# «покажи полную нотацию» and «что такое полная нотация» ask about the notation rather than set it.
_NOTATION_QUESTION = re.compile(r"\bпокажи\b|что такое|что значит|что означает|кака\w+ сейчас|расскажи")


_PREFERENCE_PATTERNS: tuple[tuple[PreferenceChange, re.Pattern[str]], ...] = (
    # Before the brevity styles: «говори краткую нотацию» names the notation, not the answer length.
    (
        PreferenceChange(notation_style=NotationStyle.SHORT),
        re.compile(
            r"(?:кратк|коротк)\w*\s+(?:ан)?нотаци|только (клетку|поле) назначения|"
            r"называй только (клетку|поле|куда)"
        ),
    ),
    (
        PreferenceChange(notation_style=NotationStyle.FULL),
        re.compile(r"полн\w*\s+(?:ан)?нотаци|обе клетки|называй обе"),
    ),
    (
        PreferenceChange(detail_level=DetailLevel.BRIEF),
        re.compile(
            r"говори кратк\w*\b(?!\s+рокиров)|говори коротко\b|отвечай кратк|покороче|"
            r"кратк(ие|о) ответ|краткост|не так подробн"
        ),
    ),
    # Before the detailed style, whose «подробность» it also contains.
    (
        PreferenceChange(detail_level=DetailLevel.NORMAL),
        re.compile(r"обычн\w* (подробност|ответ|детальност)|говори обычно"),
    ),
    (
        PreferenceChange(detail_level=DetailLevel.DETAILED),
        re.compile(r"говори подробн|отвечай подробн|подробнее|подробн(ые|о) ответ|подробност"),
    ),
    (
        PreferenceChange(pause_style=PauseStyle.EXTENDED),
        re.compile(r"говори медленн|добав(ь|ляй) пауз|делай пауз|с паузами|читай медленн|можно помедленн"),
    ),
    (
        PreferenceChange(pause_style=PauseStyle.NORMAL),
        re.compile(r"говори быстр|убери пауз|без пауз|читай быстр"),
    ),
    # Adjacency keeps a question about the board («что на доске у черных») out of
    # the orientation setting.
    (
        PreferenceChange(board_orientation=BoardOrientation.PLAYER),
        re.compile(
            r"(доск\w*|ориентаци\w*) (всегда )?(как я играю|мо(им|ему) цвет\w*|по (моему )?цвету)"
            r"|^(по )?моему цвету$"
        ),
    ),
    (
        PreferenceChange(board_orientation=BoardOrientation.WHITE),
        re.compile(
            r"(доск\w*|ориентаци\w*) (всегда (за )?|за |со стороны )бел\w+"
            r"|(покажи|показывай|разверни|поверни)\w*( доск\w*)? (за |со стороны )?бел\w+"
            r"|бел\w+ снизу|^(?:за|со стороны) бел\w+$"
        ),
    ),
    (
        PreferenceChange(board_orientation=BoardOrientation.BLACK),
        re.compile(
            r"(доск\w*|ориентаци\w*) (всегда (за )?|за |со стороны )черн\w+"
            r"|(покажи|показывай|разверни|поверни)\w*( доск\w*)? (за |со стороны )?черн\w+"
            r"|черн\w+ снизу|^(?:за|со стороны) черн\w+$"
        ),
    ),
)

# Coaching phrases are read before the control table: «где я ошибся» and «как
# оценивается позиция» would otherwise be heard as position questions.
_TRAINING_PATTERNS: tuple[tuple[TrainingQuestion, re.Pattern[str]], ...] = (
    (
        TrainingQuestion.ENABLE,
        re.compile(
            r"(включи|запусти|давай)\w*( режим)? тренер|режим тренера|будь тренером|тренируй|"
            r"^включи режим трения$"
        ),
    ),
    (
        TrainingQuestion.DISABLE,
        re.compile(
            r"(выключи|отключи|убери)\w*( режим)? тренер|без подсказок|играй честно|"
            r"^выключи (?:режим трения|стримеры)$"
        ),
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
        re.compile(
            r"как оценива|оцени позици|^оценка позиции$|какая оценка|"
            r"кто (сейчас )?лучше стоит|у кого (сейчас )?лучше"
        ),
    ),
    (
        TrainingQuestion.WHY_MOVE,
        re.compile(
            r"почему ты (так )?(сходил[а]?|пошел|пошла|ходил[а]?)|"
            r"зачем ты (так )?(сходил[а]?|пошел|пошла)|объясни свой ход"
        ),
    ),
    # Only a question about a threat: «конь эф три угрожает матом» is a move, not a request.
    (
        TrainingQuestion.THREAT,
        re.compile(
            r"^(?:а\s+)?(?:чем|кем|что|какая|какие|какую|есть ли)\b(?:\s+\w+){0,4}\s+угро[зж]\w*|"
            r"\bты\s+(?:мне\s+)?угрожа\w*|угрожа\w+\s+(?:моему|моей|мне|моим)|что ты задумал[а]?"
        ),
    ),
    (TrainingQuestion.PREVIEW, re.compile(r"что будет,? если|что если я|стоит ли (мне )?(играть|ходить)")),
    (
        TrainingQuestion.CANDIDATES,
        re.compile(
            # ASR keeps «хорошие» and invents the noun: коды, коты, сады, хиты, ходи.
            r"^хорошие\s+\w+$|хорош\w*\s+(?:ход|код|кот|сад|год|хит)\w*|"
            r"какие ходы|что мне сыграть|что лучше сыграть|"
            r"как(?:ой|ие)\s+(?:ход|код)\w*\s+лучш|лучший ход|"
            r"как мне (лучше )?сыграть|какой ход .*посовет"
        ),
    ),
    # «подскажи» — the imperative the help advertises — carries the ж stem.
    (
        TrainingQuestion.HINT,
        re.compile(
            r"подсказ|подскаж|дай совет|посоветуй|нужен совет|хорош\w+ совет|"
            r"помоги(?:те)?(?:\s+(?:мне|пожалуйста))*\s+с ходом"
        ),
    ),
)

# Review phrases are read before the control table: «продолжить разбор» would
# otherwise resume the game, and «сыграть эту позицию заново» would start one.
_REVIEW_PATTERNS: tuple[tuple[ReviewQuestion, re.Pattern[str]], ...] = (
    (ReviewQuestion.EXIT, re.compile(r"(выйти|выход|закончить|закрой|закрыть|хватит|стоп)\w*( из)? разбор")),
    (ReviewQuestion.CONTINUE, re.compile(r"продолж\w* разбор|дальше по разбору")),
    (
        ReviewQuestion.REPLAY_POSITION,
        re.compile(r"(сыграть|сыграем|переиграть|разыграть)\w* эту позицию|с этой позиции"),
    ),
    (ReviewQuestion.TURNING_POINT, re.compile(r"перелом")),
    (
        ReviewQuestion.MAIN_MISTAKE,
        re.compile(
            r"главн(ая|ую) ошибк|сам(ая|ую) больш(ая|ую) ошибк|худший ход|где я играл[а]? плохо|"
            r"^(?:кака\w+|моя)\b(?:\s+\w+){0,2}\s+ошибк\w*$"
        ),
    ),
    (
        ReviewQuestion.MISTAKE_COUNT,
        re.compile(r"сколько\b(?:\s+(?:раз|у|меня|я|было|всего|там|тут))*\s+ошиб\w*|число ошибок"),
    ),
    (ReviewQuestion.PGN, re.compile(r"\bpgn\b|\bпгн\b|покажи нотацию|партию в нотации")),
    (
        ReviewQuestion.MOVES,
        re.compile(r"продиктуй (всю )?(партию|игру)|продиктуй ходы|прочитай (партию|ходы)|назови все ходы"),
    ),
    (
        ReviewQuestion.SUMMARY,
        re.compile(
            r"^(?:алиса\s+)?(?:разбор\s*)+$|разбери (партию|игру)|(?:сделай )?разбор (всей )?партии|"
            r"^(?:а\s+)?(?:давай\s+|сделай\s+)?разбор\w*\s+(?:всей\s+)?(?:парти\w*|игр\w*)$|"
            r"^(?:а\s+)?(?:давай\s+|хочу\s+|надо\s+)?разобра(?:ть|ем|л|ла)(?:\s+(?:всю\s+)?(?:парти\w*|игр\w*))?$|"
            r"проанализируй партию|итоги партии|как я сыграл[а]?|давай разберем (?:игру|партию)|"
            r"покажи мои ошибки|^(?:каки\w+|мои)\b(?:\s+\w+){0,2}\s+ошибк\w*$|"
            r"^(?:вернуться|вернемся) к (?:предыдущ\w+|последн\w+|прошл\w+) (?:парти\w+|игр\w+)$|"
            r"^(?:последняя|предыдущая|прошлая) партия$"
        ),
    ),
)

# Puzzle phrases are read before the game commands: «еще задачу» would otherwise
# be heard as a rematch, and «выйти из задач» as a plain stop.
_PUZZLE_PATTERNS: tuple[tuple[PuzzleQuestion, re.Pattern[str]], ...] = (
    (
        PuzzleQuestion.EXIT,
        re.compile(r"(выйти|выход|закончить|хватит|стоп)\w*( из)? задач|вернут?ься к партии|вернемся к партии"),
    ),
    (
        PuzzleQuestion.SOLUTION,
        re.compile(
            r"(покажи|скажи|какое|объясни|не знаю)\w*(?: мне)? решение|сдаюсь в (?:эт\w+ )?задач\w*|решение задачи|"
            # Anchored: «не могу решить, какой ход лучше» asks the trainer, not the puzzle.
            r"(?:не (?:могу|мог|могла)|не получается)(?: \w+){0,2} реши\w+(?: эту)?(?: задач\w*)?$"
        ),
    ),
    (
        PuzzleQuestion.STREAK,
        re.compile(r"кака(я|ю)( у меня)? серия|сколько( задач)? подряд|мо(я|ю) серия"),
    ),
    (
        PuzzleQuestion.HISTORY,
        re.compile(
            r"какие (?:шахматн\w* )?задачи (?:я )?(?:решал|решила|проходил|проходила)|"
            r"сколько (?:всего )?(?:задач|головоломок) (?:я )?(?:решил|решила)|"
            r"(?:мои|моих)? ?решенн\w+ задач|история (?:моих )?(?:задач|решений)"
        ),
    ),
    (
        PuzzleQuestion.REPEAT,
        re.compile(
            # Only «задачу» takes the infinitive: «повторить позицию» reads the board.
            r"(повтори|напомни)( мне)? (задачу|задани\w+|позицию|условие)|"
            r"(повторить|напомнить)( мне)? (задачу|задани\w+)|"
            r"еще раз (задачу|задани\w+|позицию|условие)|"
            # Bare, so anchored: «какая задача, почему ты так сходил» asks the trainer.
            r"какая сейчас задача|что за задача сейчас|^какая задача$|^что за задача$|"
            r"какие задачи сейчас открыты"
        ),
    ),
    (
        PuzzleQuestion.NEXT,
        re.compile(
            r"следующ\w* задач|еще( одну)? задач|друг(ую|ая) задач|нов(ая|ую) задач|"
            # «эту задачу не хочу, другую давай»: accusative twice, unlike «в задаче другая позиция».
            r"(задачу|головоломку)( \w+){0,2} (друг|нов)ую(?! (игру|партию))"
        ),
    ),
    (
        PuzzleQuestion.START,
        re.compile(
            r"(дай|давай|покажи|предложи|начни|запусти|открой|хочу|можно|решать|решить|решим|порешаем)"
            r"\w*.*(задач|головоломк|этюд)"
            r"|^задач[аи]?$|^задачк\w+$|^(шахматн\w*|тактическ\w*) (задач|головоломк|этюд)\w*$"
            r"|^(?:режим|раздел) задач\w*$|^перейдем к задачам$"
            r"|^(шахматн\w* )?(задач|головоломк)\w* (на|моего|по|с|про)"
            r"|^(тактика|головоломка|этюд)$"
            # Anchored, so «тебе мат в 3 хода» stays a remark about the game.
            r"|^((дай|давай|хочу|покажи)\w* )?мат в (один(?: ход)?|два(?: хода)?|1(?: ход)?|2(?: хода)?)$"
            r"|одноходов|двуходов"
        ),
    ),
)

# «не надо решение, следующую задачу» refuses one puzzle command and asks for another.
_START_REFUSED = re.compile(r"\bне\s+(?:\w+\s+){0,2}(?:дай|давай|хочу|надо|нужн|буду|открыв|открой)\w*")
_SOLUTION_REFUSED = re.compile(r"\bне\s+(?:показыв|говор|читай|озвуч|объясняй)\w*(?:\s+\w+){0,2}\s+решени")
_PUZZLE_ASKED = re.compile(r"\b(?:дай|давай|хочу|надо|нужн|буду|открыв|открой|покажи|скажи|объясни)\w*")
_PUZZLE_WANTED = re.compile(r"задач|головоломк|этюд|решени|друг(?:ую|ая)")
_REFUSAL_AGAIN = re.compile(
    r"\bне\s+(?:\w+\s+){0,2}(?:дай|давай|хочу|надо|нужн|буду|открыв|открой|покажи|скажи|объясни)\w*"
)
_REFUSAL_JUST_BEFORE = re.compile(r"\bне\s+(?:\w+\s+)?$")
_REFUSAL_WORD = re.compile(r"\bне\b")

# Themes the shipped catalogue actually carries, named the way a player names them.
_PUZZLE_THEMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mateIn1", re.compile(r"мат в один|мат в 1\b|одноходов")),
    ("mateIn2", re.compile(r"мат в два|мат в 2\b|двуходов")),
    ("fork", re.compile(r"вилк")),
    ("pin", re.compile(r"связк")),
    ("skewer", re.compile(r"сквозн|шампур")),
    ("backRankMate", re.compile(r"последн\w* горизонтал")),
    ("discoveredAttack", re.compile(r"вскрыт")),
    ("hangingPiece", re.compile(r"висяч|зевок")),
)

# What is left of a preview question once the framing words are dropped.
_PREVIEW_PREFIX = re.compile(
    r"^.*?(?:если (?:я )?(?:сыграю|пойду|походу|сделаю ход)?|стоит ли (?:мне )?(?:играть|ходить))\s*"
)

_REMATCH = re.compile(
    r"реванш|еще (одну )?(партию|игру)|следующ\w* (игр|парт)|сыграем еще|сыграем сложнее|"
    r"сложнее|потруднее|усложни|смен(и|им|ить) цвет|поменя\w* цвет|другим цветом"
)
_REMATCH_SWAP = re.compile(r"друг(им|ой) цвет|смен(и|им|ить) цвет|поменя\w* цвет|другой стороной")
_REMATCH_HARDER = re.compile(r"сложнее|потруднее|усложни|уровень выше|посильнее|потяжелее")
_REMATCH_WHITE = re.compile(r"\bбел(ыми|ые)\b")
_REMATCH_BLACK = re.compile(r"\bчерн(ыми|ые)\b")

_AFFIRM_EXPLICIT = re.compile(r"^(?:да|ага|верно|точно|правильно|подтверждаю|да подтверждаю)$")
_AFFIRM_FRIENDLY = re.compile(r"^(?:угу|конечно|давай|да давай|да конечно)$")
_AFFIRM_CONTINUE = re.compile(r"^(?:поехали|погнали|начали|начинаем|продолжать|продолжи партию)$")
_DECLINE = re.compile(
    r"^(?:нет|не|отмена|неверно|неправильно|не надо|давай не будем|я передумал[а]?|"
    r"я не это имел[а]? в виду|не это)$"
)


def confirmation_answer(utterance: str, pending_kind: CommandKind | None = None) -> bool | None:
    """Return a context-safe confirmation answer for the pending action."""
    text = normalize(utterance).text
    if _DECLINE.match(text):
        return False
    if _AFFIRM_EXPLICIT.match(text):
        return True
    if pending_kind is not None and pending_kind is not CommandKind.RESIGN and _AFFIRM_FRIENDLY.match(text):
        return True
    if pending_kind is CommandKind.CONTINUE and _AFFIRM_CONTINUE.match(text):
        return True
    return None


def route(
    utterance: str,
    board: chess.Board | None = None,
    pending: PendingClarification | None = None,
    last_heard: str | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> RoutedCommand:
    """Classify `utterance`; `board` is `None` when there is no game to move in."""
    routed = _route_once(utterance, board, pending, last_heard, confidence_threshold)
    if routed.kind is not CommandKind.UNKNOWN:
        return routed
    relayed = False
    addressed = routed.normalized.text
    for _ in range(len(_VOCATIVES)):
        shorter = _RELAY_TO_YURA.match(addressed)
        if shorter is not None:
            relayed = True
            addressed = addressed[shorter.end() :]
            continue
        without = _without_vocative(tuple(addressed.split()))
        if without is None:
            break
        addressed = without
    if addressed == routed.normalized.text or not addressed:
        return routed
    retried = _route_once(addressed, board, pending, last_heard, confidence_threshold)
    if retried.kind is not CommandKind.UNKNOWN:
        return replace(retried, normalized=routed.normalized, addressed=addressed)
    if relayed:
        return replace(routed, kind=CommandKind.PERSONA, persona=PersonaRequest(PersonaWish.WHO))
    return routed


def _route_once(
    utterance: str,
    board: chess.Board | None,
    pending: PendingClarification | None,
    last_heard: str | None,
    confidence_threshold: float,
) -> RoutedCommand:
    normalized = normalize(utterance)

    # A retraction takes back a command as readily as a move: «покажи доску, ой
    # нет, пешка е два е четыре» asks for the move only, and «отмени ход, ой нет»
    # asks for nothing at all — what was taken back is never read again.
    retraction = _after_retraction(utterance)
    if retraction is not None:
        head, tail = retraction
        if tail:
            replacement = route(tail, board, pending, last_heard, confidence_threshold)
            if replacement.kind is not CommandKind.UNKNOWN:
                return replacement
        if normalize(head).has_move_tokens:
            return RoutedCommand(
                CommandKind.CLARIFY,
                normalized,
                clarification=PendingClarification(heard=normalized.text),
            )
        return RoutedCommand(CommandKind.UNKNOWN, normalized)

    if is_rules_request(normalized.text):
        return RoutedCommand(CommandKind.HELP, normalized, clarification=None)

    for kind, pattern in _HELP_PATTERNS:
        if pattern.search(normalized.text):
            return RoutedCommand(kind, normalized, clarification=None)

    preference = parse_preference(normalized.text)
    if preference is not None:
        return RoutedCommand(CommandKind.PREFERENCE, normalized, preference=preference, clarification=None)
    puzzle = parse_puzzle(normalized.text)
    if puzzle is not None:
        return RoutedCommand(CommandKind.PUZZLE, normalized, puzzle=puzzle, clarification=None)
    rematch = parse_rematch(normalized.text)
    if rematch is not None:
        return RoutedCommand(CommandKind.REMATCH, normalized, rematch=rematch, clarification=None)
    review = parse_review(normalized.text)
    if review is not None:
        return RoutedCommand(CommandKind.REVIEW, normalized, review=review, clarification=None)
    training = parse_training(normalized.text)
    if training is not None:
        return RoutedCommand(CommandKind.TRAINING, normalized, training=training, clarification=None)

    for kind, pattern in _CONVERSATION_PATTERNS:
        if pattern.search(normalized.text):
            if kind in _LEAVING_KINDS and _LEAVING_NEGATED.search(normalized.text):
                continue
            persona_asked = parse_persona(normalized.text) if kind is CommandKind.PERSONA else None
            if kind is CommandKind.PERSONA and persona_asked is None:
                continue
            return RoutedCommand(kind, normalized, persona=persona_asked, clarification=None)

    for kind, pattern in _CONTROL_PATTERNS:
        if pattern.search(normalized.text):
            if kind is CommandKind.POSITION_QUERY and _RULES_FRAME.search(normalized.text):
                # «может ли пешка превратиться на восьмой горизонтали» names a
                # rank and «как сделать первый ход» a move number, but both ask
                # a rule the board reader has no answer to.
                return RoutedCommand(CommandKind.HELP, normalized, clarification=None)
            if kind in _LEAVING_KINDS and _LEAVING_NEGATED.search(normalized.text):
                continue
            if kind is CommandKind.EXIT_CONFIRM and _LEAVING_ASKED_AS_A_RULE.search(normalized.text):
                continue
            if kind is CommandKind.RESIGN:
                clause = _last_clause(normalized.text)
                if _SURRENDER_SPOKEN.search(normalized.text):
                    if not _surrender_meant(clause):
                        continue
                else:
                    if _END_GAME_DEFINITION.search(clause):
                        return RoutedCommand(CommandKind.HELP, normalized, clarification=None)
                    if _END_GAME_NEGATED.search(clause):
                        # The refusal leaves whatever follows it to route.
                        continue
            colour_asked = parse_color_choice(normalized.text) if kind is CommandKind.COLOR_CHOICE else None
            if kind is CommandKind.COLOR_CHOICE and colour_asked is None:
                # A colour named without asking for it: let the later patterns read it.
                continue
            screen_asked = parse_screen(normalized.text) if kind is CommandKind.SCREEN else None
            level_asked = parse_level(normalized.text) if kind in _LEVEL_KINDS else None
            if kind is CommandKind.LEVEL and level_asked is None:
                continue
            if kind is CommandKind.LEVEL_QUERY:
                # «изменить уровень на пять» names the level it wants; a bare question stays a question.
                if level_asked is not None and level_asked.intent is LevelIntent.SET:
                    kind = CommandKind.LEVEL
                else:
                    level_asked = None
            heard = last_heard if kind is CommandKind.REPEAT_HEARD else None
            # A control command answers the clarification by replacing it.
            return RoutedCommand(
                kind,
                normalized,
                heard=heard,
                clarification=None,
                rematch=colour_asked,
                level=level_asked,
                screen=screen_asked,
                undo_count=_undo_count(normalized.text) if kind is CommandKind.UNDO else 1,
            )

    if pending is not None:
        answered = _answer_clarification(normalized, pending)
        if answered is not None:
            return answered

    if board is None:
        return RoutedCommand(CommandKind.UNKNOWN, normalized)

    corrected = _corrected_move(utterance, normalized, board, confidence_threshold)
    if corrected is not None:
        return corrected

    if _INCOMPLETE_MOVE.fullmatch(normalized.text) or contains_multiple_moves(normalized):
        return RoutedCommand(
            CommandKind.CLARIFY,
            normalized,
            clarification=PendingClarification(heard=normalized.text),
        )

    resolution = resolve(normalized, board)
    return _from_resolution(normalized, resolution, board, confidence_threshold)


# Yura is asked to act as if he were a third person in the room. What follows the
# request is the command; only when nothing follows is the question about him.
_RELAY_TO_YURA = re.compile(r"(?:скажи|передай) юр[еы]\s+(?:то что\s+)?|спроси у юры\s+|пусть юра\s+")


def _without_vocative(words: tuple[str, ...]) -> str | None:
    """The utterance with the name it was addressed to removed, or `None` if no name was said.

    «юра пока» and «твой ход юра» are the commands the skill already answers,
    said to the player's opponent by name. A filler is dropped only when a name
    follows it: «ну юра, твой ход» is addressed, «ну хватит» is not.
    """
    opening = 0
    while opening < len(words) and words[opening] in _ADDRESS_FILLERS:
        opening += 1
    named = opening < len(words) and words[opening] in _VOCATIVES
    trimmed = words[opening + 1 :] if named else words
    if trimmed and trimmed[-1] in _VOCATIVES:
        trimmed, named = trimmed[:-1], True
    return " ".join(trimmed) if named and trimmed else None


def parse_preference(text: str) -> PreferenceChange | None:
    """Read a settings command, or return `None` when the phrase is not one."""
    sound = _sound_preference(text)
    if sound is not None:
        return sound
    asked_about = _NOTATION_QUESTION.search(text) is not None
    for change, pattern in _PREFERENCE_PATTERNS:
        if pattern.search(text):
            return None if asked_about and change.notation_style is not None else change
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


def parse_review(text: str) -> ReviewRequest | None:
    """Read a question about a finished game, or return `None` when it is not one."""
    for question, pattern in _REVIEW_PATTERNS:
        if pattern.search(text):
            return ReviewRequest(question)
    return None


def parse_puzzle(text: str) -> PuzzleRequest | None:
    """Read a puzzle command, or return `None` when the phrase is not one."""
    for question, pattern in _PUZZLE_PATTERNS:
        if not pattern.search(text) or _puzzle_refused(question, text):
            continue
        if question not in {PuzzleQuestion.START, PuzzleQuestion.NEXT}:
            return PuzzleRequest(question)
        return PuzzleRequest(question, theme=_puzzle_theme(text))
    return None


def _puzzle_refused(question: PuzzleQuestion, text: str) -> bool:
    if question is PuzzleQuestion.SOLUTION:
        refused = _SOLUTION_REFUSED.search(text)
    elif question is PuzzleQuestion.START:
        refused = _START_REFUSED.search(text) or _SOLUTION_REFUSED.search(text)
    else:
        return False
    return bool(refused) and not _asked_plainly(text)


def _asked_plainly(text: str) -> bool:
    """Whether a puzzle is still asked for outright, as «не хочу ждать, дай задачу» asks.

    The verb has to want a puzzle: «не давай задачу, покажи доску» asks for the board.
    """
    for asked in _PUZZLE_ASKED.finditer(text):
        if _REFUSAL_JUST_BEFORE.search(text[: asked.start()]):
            continue
        if _PUZZLE_WANTED.search(_after(text, asked) or _before(text, asked)):
            return True
    return False


def _after(text: str, asked: re.Match[str]) -> str:
    """What the verb asks for, up to the next refusal; empty when the verb ends the phrase."""
    wanted = text[asked.end() :]
    refused_again = _REFUSAL_AGAIN.search(wanted)
    return wanted[: refused_again.start()] if refused_again else wanted


def _before(text: str, asked: re.Match[str]) -> str:
    """What «лучше задачу дай» asks for: named first, since the last refusal."""
    said = text[: asked.start()]
    refusals = list(_REFUSAL_WORD.finditer(said))
    return said[refusals[-1].end() :] if refusals else said


def _puzzle_theme(text: str) -> str | None:
    for theme, pattern in _PUZZLE_THEMES:
        if pattern.search(text):
            return theme
    return None


def parse_color_choice(text: str) -> RematchRequest | None:
    """Read the colour the player asks to play, or `None` when none is named."""
    match = _COLOR_CHOICE.search(text)
    if match is None:
        return None
    # «мне играть белыми или черными» asks which side to take, not for one.
    if _COLOR_ALTERNATIVE.search(text) or _COLOR_ADVICE.search(text):
        return None
    if _COLOR_FOR_ENGINE.search(text[: match.end()]):
        return None
    refusal = _COLOR_REFUSAL.search(text)
    if refusal is not None:
        # «не буду играть черными» refuses a colour without naming another.
        match = _COLOR_RETRY.search(text, refusal.end())
        if match is None:
            return None
    stem = match.group("color") or match.groupdict().get("second")
    return RematchRequest(color=RematchColor.WHITE if stem == "бел" else RematchColor.BLACK)


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
    """Handle an explicit yes/no or a named promotion piece; anything else is re-read as a fresh utterance."""
    if _DECLINE.match(normalized.text):
        return RoutedCommand(CommandKind.CANCEL_CLARIFY, normalized, clarification=None)
    promoted = _answer_promotion(normalized, pending)
    if promoted is not None:
        return RoutedCommand(CommandKind.MOVE, normalized, move=promoted, clarification=None)
    if _AFFIRM_EXPLICIT.match(normalized.text):
        if len(pending.candidates) == 1:
            return RoutedCommand(CommandKind.MOVE, normalized, move=pending.candidates[0], clarification=None)
        # «да» cannot pick between several candidates; keep waiting.
        return RoutedCommand(CommandKind.CLARIFY, normalized, clarification=pending)
    return None


def _answer_promotion(normalized: Normalized, pending: PendingClarification) -> str | None:
    """The candidate a bare piece name picks when only the promotion is still open."""
    if promotion_choice(pending.candidates) is None:
        return None
    if len(normalized.signature) != 1 or normalized.signature[0].kind is not TokenKind.PIECE:
        return None
    letter = normalized.signature[0].value.lower()
    for candidate in pending.candidates:
        if candidate[4] == letter:
            return candidate
    return None


def _after_retraction(utterance: str) -> tuple[str, str] | None:
    """What was taken back and what replaced it, or `None` when nothing was."""
    lowered = utterance[:MAX_UTTERANCE_LENGTH].lower().replace("ё", "е")
    parts = _RETRACTION.split(lowered)
    if len(parts) < 2:
        return None
    head, tail = parts[0].strip(" ,;"), parts[-1].strip(" ,;")
    if not head:
        return None
    return head, tail


def _corrected_move(
    utterance: str,
    normalized: Normalized,
    board: chess.Board,
    confidence_threshold: float,
) -> RoutedCommand | None:
    """Read a move the speaker narrowed mid-sentence, or `None` when there is none.

    «ферзь b1 b2, b1 b3» does not retract anything outright: the last reading is
    the likely one, but the doubt is real, so it is offered for confirmation
    rather than applied. An outright retraction never reaches here — `route`
    reads what followed it before any of the pattern tables run.
    """
    if not contains_multiple_moves(normalized):
        return None
    lowered = utterance[:MAX_UTTERANCE_LENGTH].lower().replace("ё", "е")
    segments = _SEGMENT.split(lowered)
    if len(segments) < 2:
        return None

    candidate = normalize(segments[-1])
    if not candidate.has_move_tokens or contains_multiple_moves(candidate):
        return None
    resolution = resolve(candidate, board)
    if resolution.status is not ResolutionStatus.RESOLVED or resolution.move is None:
        return None
    if resolution.confidence < confidence_threshold:
        return None
    return RoutedCommand(
        CommandKind.CLARIFY,
        normalized,
        resolution=resolution,
        clarification=PendingClarification(heard=normalized.text, candidates=(resolution.move,)),
    )


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
        explanation = explain(resolution.recognized, board)
        if explanation.reason is IllegalReason.UNCLEAR:
            return RoutedCommand(
                CommandKind.CLARIFY,
                normalized,
                resolution=resolution,
                clarification=PendingClarification(heard=normalized.text),
            )
        return RoutedCommand(CommandKind.ILLEGAL_MOVE, normalized, resolution=resolution, explanation=explanation)
    # Ambiguous and low-confidence readings wait for the player instead of
    # touching the game.
    return RoutedCommand(
        CommandKind.CLARIFY,
        normalized,
        resolution=resolution,
        clarification=PendingClarification(heard=normalized.text, candidates=resolution.candidates),
    )


def _last_clause(text: str) -> str:
    """What the utterance ends on, once it has moved past its own preamble."""
    return _LAST_CLAUSE.split(text)[-1]


def _surrender_meant(clause: str) -> bool:
    """Whether the game is given up outright, as «я не сдаюсь» refuses to."""
    return any(_SURRENDER_REFUSED.search(clause[: said.start()]) is None for said in _SURRENDER_SPOKEN.finditer(clause))


def _undo_count(text: str) -> int:
    match = _UNDO_COUNT.search(text)
    if match is None:
        return 1
    value = match.group("count")
    count = int(value) if value.isdigit() else _COUNT_VALUES[value]
    return max(1, min(count, 20))


def contains_multiple_moves(normalized: Normalized) -> bool:
    parts = _MOVE_SEQUENCE.split(normalized.text, maxsplit=1)
    if len(parts) == 2 and all(normalize(part).has_move_tokens for part in parts):
        return True
    squares = sum(token.kind is TokenKind.SQUARE for token in normalized.signature)
    pieces = sum(token.kind is TokenKind.PIECE for token in normalized.signature)
    if squares > 2:
        return True
    if squares >= 2 and pieces >= 2:
        return True
    return squares >= 2 and bool(re.search(r"\b(ваш|твой) ход\b", normalized.text))
