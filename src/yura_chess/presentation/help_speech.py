"""Read the command catalogue aloud, one topic and one page at a time.

The old help was a single long sentence: unusable by ear. Here the commands are
split into named topics, each topic is read in short pages, and the whole
catalogue is available as its own paged view. What the caller keeps between
turns is only the current topic and page — the help never touches the game.

Navigation words («дальше», «назад», «сначала») are matched anchored, so they
only mean navigation when help is open and the whole utterance is that word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from yura_chess.presentation.move_speech import Speech
from yura_chess.voice.normalizer import normalize

# Three short lines is about as much as stays in the ear from one reply.
LINES_PER_PAGE = 3


class HelpTopic(StrEnum):
    MOVES = "moves"
    POSITION = "position"
    GAME = "game"
    SPEECH = "speech"
    # Not a section of its own: the whole catalogue, read page by page.
    ALL = "all"


class HelpMode(StrEnum):
    """What the player is in the middle of, which decides the opening line."""

    NO_GAME = "no_game"
    GAME = "game"
    TRAINING = "training"
    GAME_OVER = "game_over"
    PUZZLE = "puzzle"


@dataclass(frozen=True, slots=True)
class HelpSection:
    topic: HelpTopic
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HelpState:
    """Where the reading stopped; `topic` is `None` while the menu is shown."""

    topic: HelpTopic | None = None
    page: int = 0


@dataclass(frozen=True, slots=True)
class HelpAnswer:
    speech: Speech
    # `None` closes the help; any other value keeps it open at that place.
    state: HelpState | None


SECTIONS: tuple[HelpSection, ...] = (
    HelpSection(
        HelpTopic.MOVES,
        "ходы",
        (
            "Ход можно назвать так: «пешка е два е четыре», «конь эф три» или просто «е два е четыре».",
            "Если я переспрошу ход, ответьте «да» или «нет» либо назовите ход точнее.",
            "«Отмени ход» возвращает последний полный ход.",
        ),
    ),
    HelpSection(
        HelpTopic.POSITION,
        "позиция",
        (
            "«Какая позиция» читает доску по две горизонтали, «дальше» продолжает чтение.",
            "«Что на е четыре», «где белые слоны», «чей ход» и «есть ли шах» отвечают о текущей позиции.",
            "«Какой был последний ход» и «что делали черные четыре хода назад» читают историю.",
        ),
    ),
    HelpSection(
        HelpTopic.GAME,
        "партия",
        (
            "«Новая игра черными уровень десять» начинает партию, уровень от нуля до двадцати.",
            "«Продолжить последнюю партию» возвращает к незаконченной партии.",
            "«Предлагаю ничью» и «сдаюсь» завершают партию, я переспрошу перед этим.",
            "«Какой уровень» называет текущую сложность.",
        ),
    ),
    HelpSection(
        HelpTopic.SPEECH,
        "речь",
        (
            "«Что ты услышала» повторяет распознанную фразу.",
            "«Повтори медленно» читает мой прошлый ответ по словам.",
            "«Повтори координаты по буквам» проговаривает поле по буквам.",
        ),
    ),
)

_SECTIONS_BY_TOPIC = {section.topic: section for section in SECTIONS}

_MODE_OPENINGS: dict[HelpMode, str] = {
    HelpMode.NO_GAME: "Партия еще не начата. Скажите «новая игра белыми уровень пять», чтобы начать.",
    HelpMode.GAME: "Идет партия. Назовите ход, например «пешка е два е четыре».",
    HelpMode.TRAINING: "Идет тренировка. Назовите ход или спросите совет.",
    HelpMode.GAME_OVER: "Партия закончена. Скажите «новая игра», чтобы сыграть еще.",
    HelpMode.PUZZLE: "Идет задача. Назовите ход решения.",
}

_TOPIC_ALIASES: tuple[tuple[HelpTopic, re.Pattern[str]], ...] = (
    (HelpTopic.ALL, re.compile(r"^(все|весь|всё|полн|список|команд)")),
    (HelpTopic.MOVES, re.compile(r"^(ход|фигур)")),
    (HelpTopic.POSITION, re.compile(r"^(позиц|доск)")),
    (HelpTopic.GAME, re.compile(r"^(парти|игр|уров|сложност)")),
    (HelpTopic.SPEECH, re.compile(r"^(реч|повтор|распозна|произнош)")),
)

# Words that only ask for help; what is left after them names the topic.
_TRIGGER_WORDS = frozenset(
    {
        "справка",
        "справку",
        "справке",
        "справки",
        "помощь",
        "помоги",
        "подскажи",
        "расскажи",
        "скажи",
        "что",
        "чем",
        "ты",
        "умеешь",
        "можешь",
        "как",
        "играть",
        "по",
        "о",
        "об",
        "про",
        "мне",
        "какие",
        "какая",
        "есть",
        "можно",
        "сказать",
        "раздел",
        "разделы",
        "разделе",
        "твои",
        "твоя",
        "а",
        "и",
    }
)

_NEXT = re.compile(r"^(дальше|далее|еще|ещё|следующ\w*|дальнейшее)$")
_PREVIOUS = re.compile(r"^(назад|обратно|предыдущ\w*)$")
_RESTART = re.compile(r"^(сначала|с начала|заново|в начало|начало)$")

_CONTINUATION = " Скажите «дальше», чтобы продолжить."
_ENDING = " Это конец раздела. Назовите другой раздел или скажите «выйти из справки»."


def answer_help(utterance: str, mode: HelpMode, state: HelpState | None = None) -> HelpAnswer:
    """Answer a help request; `state` is where the previous help reply stopped."""
    words = [word for word in normalize(utterance).words if word not in _TRIGGER_WORDS]
    topic = _match_topic(words)
    if topic is not None:
        return _render(topic, 0)
    if words:
        return _unknown_topic()
    if state is not None and state.topic is not None:
        return _render(state.topic, state.page)
    return _menu(mode)


def navigate(utterance: str, state: HelpState, mode: HelpMode) -> HelpAnswer | None:
    """Move within the open help, or return `None` when this is not navigation."""
    text = normalize(utterance).text
    if state.topic is None:
        # The menu is a single page: any navigation starts the whole catalogue.
        if _NEXT.match(text) or _RESTART.match(text):
            return _render(HelpTopic.ALL, 0)
        if _PREVIOUS.match(text):
            return _menu(mode)
        return None
    if _NEXT.match(text):
        return _render(state.topic, state.page + 1)
    if _PREVIOUS.match(text):
        return _render(state.topic, state.page - 1)
    if _RESTART.match(text):
        return _render(state.topic, 0)
    return None


def bare_topic(utterance: str) -> HelpAnswer | None:
    """Open a section named on its own, e.g. «ходы» after the menu offered it.

    Only a single word counts, so «какая позиция» stays a question about the
    board even while the help is open.
    """
    words = normalize(utterance).words
    if len(words) != 1:
        return None
    topic = _match_topic(list(words))
    return None if topic is None else _render(topic, 0)


def restore(topic: str | None, page: int) -> HelpState | None:
    """Rebuild the reading position a client sent back.

    A missing topic means the menu is open; a topic this build no longer has
    closes the help instead of forcing the player into it.
    """
    if topic is None:
        return HelpState(topic=None, page=0)
    try:
        section = HelpTopic(topic)
    except ValueError:
        return None
    return HelpState(topic=section, page=max(0, min(page, page_count(section) - 1)))


def page_count(topic: HelpTopic) -> int:
    return max(1, -(-len(_lines(topic)) // LINES_PER_PAGE))


def close() -> HelpAnswer:
    return HelpAnswer(Speech.of("Закрываю справку. Назовите ход или команду."), None)


def _menu(mode: HelpMode) -> HelpAnswer:
    titles = ", ".join(section.title for section in SECTIONS)
    text = (
        f"{_MODE_OPENINGS[mode]} Разделы справки: {titles}. "
        "Назовите раздел или скажите «все команды», чтобы услышать весь список."
    )
    return HelpAnswer(Speech.of(text), HelpState(topic=None, page=0))


def _unknown_topic() -> HelpAnswer:
    titles = ", ".join(section.title for section in SECTIONS)
    text = f"Такого раздела в справке нет. Есть разделы: {titles}. Или скажите «все команды»."
    return HelpAnswer(Speech.of(text), HelpState(topic=None, page=0))


def _render(topic: HelpTopic, page: int) -> HelpAnswer:
    lines = _lines(topic)
    pages = page_count(topic)
    page = max(0, min(page, pages - 1))
    chunk = lines[page * LINES_PER_PAGE : (page + 1) * LINES_PER_PAGE]
    has_next = page + 1 < pages
    heading = "Все команды." if topic is HelpTopic.ALL else f"Раздел «{_SECTIONS_BY_TOPIC[topic].title}»."
    opening = f"{heading} " if page == 0 else ""
    text = opening + " ".join(chunk) + (_CONTINUATION if has_next else _ENDING)
    return HelpAnswer(Speech.of(text), HelpState(topic=topic, page=page))


def _lines(topic: HelpTopic) -> tuple[str, ...]:
    if topic is not HelpTopic.ALL:
        return _SECTIONS_BY_TOPIC[topic].lines
    catalogue: list[str] = []
    for section in SECTIONS:
        catalogue.append(f"Раздел «{section.title}».")
        catalogue.extend(section.lines)
    return tuple(catalogue)


def _match_topic(words: list[str]) -> HelpTopic | None:
    for word in words:
        for topic, pattern in _TOPIC_ALIASES:
            if pattern.match(word):
                return topic
    return None
