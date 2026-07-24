"""«Помощь» and «что ты умеешь» must always be an instruction.

Yandex moderation asks both phrases in an arbitrary state — including in the
middle of a game — and rejects a reply that opens with the state («Идет
партия») instead of saying what the skill is and how to use it. The state is
still told, but last, together with the command that is expected next.
"""

from __future__ import annotations

import pytest

from yura_chess.adapters.alice.models import TEXT_LIMIT, TTS_LIMIT
from yura_chess.domain.preferences import PauseStyle
from yura_chess.presentation.help_speech import SKILL_INTRO, HelpMode, answer_help
from yura_chess.presentation.move_speech import add_pauses


@pytest.mark.parametrize("utterance", ["помощь", "что ты умеешь", "что ты умеешь делать", "справка"])
@pytest.mark.parametrize("mode", list(HelpMode))
def test_help_opens_with_the_instruction_in_every_mode(utterance: str, mode: HelpMode) -> None:
    answer = answer_help(utterance, mode)

    assert answer.speech.text.startswith(SKILL_INTRO)
    assert "«новая игра белыми уровень пять»" in answer.speech.text
    assert "«пешка е два е четыре»" in answer.speech.text
    assert "Разделы справки" in answer.speech.text


@pytest.mark.parametrize(
    ("mode", "tail"),
    [
        (
            HelpMode.NO_GAME,
            "Сейчас партия не начата. Когда закончите со справкой, жду команду «новая игра белыми уровень пять».",
        ),
        (
            HelpMode.GAME,
            "Сейчас идет партия. Когда закончите со справкой, жду ваш ход, например «пешка е два е четыре».",
        ),
        (
            HelpMode.TRAINING,
            "Сейчас идет партия с тренером. Когда закончите со справкой, жду ваш ход или вопрос, например «подскажи».",
        ),
        (
            HelpMode.GAME_OVER,
            "Партия закончена. Когда закончите со справкой, жду команду «новая игра» или «разбери партию».",
        ),
        (
            HelpMode.PUZZLE,
            "Сейчас открыта задача. Когда закончите со справкой, жду ход решения или команду «покажи решение».",
        ),
    ],
)
def test_help_ends_with_the_current_state_and_the_expected_command(mode: HelpMode, tail: str) -> None:
    # The state closes the reply instead of opening it: as an opening it read as
    # a move prompt, at the end it tells the player what to say next.
    assert answer_help("помощь", mode).speech.text.endswith(tail)


@pytest.mark.parametrize("mode", list(HelpMode))
def test_help_menu_fits_the_alice_limits(mode: HelpMode) -> None:
    # The longest form: extended pauses add markup to every sentence end.
    speech = add_pauses(answer_help("помощь", mode).speech, PauseStyle.EXTENDED)

    assert len(speech.text) <= TEXT_LIMIT
    assert len(speech.spoken()) <= TTS_LIMIT
