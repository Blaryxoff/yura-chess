"""Name the opening and the stage of the game.

Both answers are read-only: they look at the canonical history and the current
material, never at the engine, and never touch the board they are given. An
unrecognised line is answered honestly rather than guessed at — the shipped ECO
set is compact, so «дебют не определён» is a normal answer, not a failure.

The opening set is the offline CC0 import in `yura_chess/data/openings.tsv`;
runtime never reaches for the source repository.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib.resources import files

import chess

from yura_chess.presentation.move_speech import Speech

_OPENINGS_RESOURCE = ("yura_chess", "data", "openings.tsv")

# Speelman's threshold: the endgame has started once neither side has more than
# thirteen points of material besides pawns and the king.
_ENDGAME_MATERIAL = 13
_PIECE_VALUES: dict[int, int] = {
    chess.PAWN: 0,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

# The opening lasts while the pieces are still coming out: ten full moves at
# most, and only until six of the eight minor pieces have left home.
_OPENING_PLIES = 20
_UNDEVELOPED_MINORS = 3
_MINOR_HOME_SQUARES: tuple[tuple[int, chess.Color, int], ...] = (
    (chess.B1, chess.WHITE, chess.KNIGHT),
    (chess.G1, chess.WHITE, chess.KNIGHT),
    (chess.C1, chess.WHITE, chess.BISHOP),
    (chess.F1, chess.WHITE, chess.BISHOP),
    (chess.B8, chess.BLACK, chess.KNIGHT),
    (chess.G8, chess.BLACK, chess.KNIGHT),
    (chess.C8, chess.BLACK, chess.BISHOP),
    (chess.F8, chess.BLACK, chess.BISHOP),
)


class GameStage(StrEnum):
    OPENING = "opening"
    MIDDLEGAME = "middlegame"
    ENDGAME = "endgame"


@dataclass(frozen=True, slots=True)
class OpeningName:
    eco: str
    opening: str
    variation: str


_STAGE_NAMES: dict[GameStage, str] = {
    GameStage.OPENING: "дебют",
    GameStage.MIDDLEGAME: "миттельшпиль",
    GameStage.ENDGAME: "эндшпиль",
}

# The source catalogue uses English names. Alice speaks Russian, so common
# opening families are translated here; an unmapped rare family is identified
# honestly by ECO code instead of being pronounced as broken English TTS.
# A family the catalogue narrows with an English move suffix («…, with e3») maps
# to its base Russian name: the ECO code already carries the extra precision, and
# the suffix has no Russian form that can be spoken.
_RUSSIAN_OPENINGS: dict[str, str] = {
    "Alekhine Defense": "Защита Алехина",
    "Amar Opening": "Дебют Амара",
    "Amazon Attack": "Атака амазонки",
    "Amsterdam Attack": "Амстердамская атака",
    "Anderssen's Opening": "Дебют Андерсена",
    "Australian Defense": "Австралийская защита",
    "Barnes Defense": "Защита Барнса",
    "Barnes Opening": "Дебют Барнса",
    "Basque Opening": "Баскский дебют",
    "Benko Gambit": "Гамбит Бенко",
    "Benko Gambit Accepted": "Принятый гамбит Бенко",
    "Benko Gambit Declined": "Отказанный гамбит Бенко",
    "Benoni Defense": "Защита Бенони",
    "Bird Opening": "Дебют Бёрда",
    "Bishop's Opening": "Дебют слона",
    "Blackmar-Diemer Gambit": "Гамбит Блэкмара — Димера",
    "Blackmar-Diemer Gambit Accepted": "Принятый гамбит Блэкмара — Димера",
    "Blackmar-Diemer Gambit Declined": "Отказанный гамбит Блэкмара — Димера",
    "Blumenfeld Countergambit": "Контргамбит Блюменфельда",
    "Blumenfeld Countergambit Accepted": "Принятый контргамбит Блюменфельда",
    "Bogo-Indian Defense": "Защита Боголюбова",
    "Bongcloud Attack": "Атака Бонгклауд",
    "Borg Defense": "Защита Борга",
    "Caro-Kann Defense": "Защита Каро — Канн",
    "Carr Defense": "Защита Карра",
    "Catalan Opening": "Каталонское начало",
    "Center Game": "Центральный дебют",
    "Center Game Accepted": "Принятый центральный дебют",
    "Clemenz Opening": "Дебют Клеменца",
    "Colle System": "Система Колле",
    "Czech Defense": "Чешская защита",
    "Danish Gambit": "Датский гамбит",
    "Danish Gambit Accepted": "Принятый датский гамбит",
    "Danish Gambit Declined": "Отказанный датский гамбит",
    "Dresden Opening": "Дрезденский дебют",
    "Duras Gambit": "Гамбит Дураса",
    "Dutch Defense": "Голландская защита",
    "Döry Defense": "Защита Дьёри",
    "East Indian Defense": "Восточноиндийская защита",
    "Elephant Gambit": "Гамбит слона",
    "English Defense": "Английская защита",
    "English Opening": "Английское начало",
    "English Orangutan": "Английский орангутанг",
    "Englund Gambit": "Гамбит Энглунда",
    "Englund Gambit Declined": "Отказанный гамбит Энглунда",
    "Four Knights Game": "Дебют четырёх коней",
    "French Defense": "Французская защита",
    "Goldsmith Defense": "Защита Голдсмита",
    "Grob Opening": "Дебют Гроба",
    "Grünfeld Defense": "Защита Грюнфельда",
    "Gunderam Defense": "Защита Гундерама",
    "Hippopotamus Defense": "Защита гиппопотама",
    "Horwitz Defense": "Защита Горвица",
    "Hungarian Opening": "Венгерский дебют",
    "Indian Defense": "Индийская защита",
    "Irish Gambit": "Ирландский гамбит",
    "Italian Game": "Итальянская партия",
    "Kangaroo Defense": "Защита кенгуру",
    "King's Gambit": "Королевский гамбит",
    "King's Gambit Accepted": "Принятый королевский гамбит",
    "King's Gambit Declined": "Отказанный королевский гамбит",
    "King's Indian Attack": "Староиндийское начало",
    "King's Indian Attack, with Bf5": "Староиндийское начало",
    "King's Indian Attack, with e6": "Староиндийское начало",
    "King's Indian Defense": "Староиндийская защита",
    "King's Knight Opening": "Дебют королевского коня",
    "King's Pawn Game": "Дебют королевской пешки",
    "King's Pawn Opening": "Дебют королевской пешки",
    "Kádas Opening": "Дебют Кадаша",
    "Latvian Gambit": "Латышский гамбит",
    "Latvian Gambit Accepted": "Принятый латышский гамбит",
    "Lemming Defense": "Защита лемминга",
    "Lion Defense": "Защита льва",
    "London System": "Лондонская система",
    "London System, with Bd3": "Лондонская система",
    "London System, with Be2": "Лондонская система",
    "Marienbad System": "Система Мариенбада",
    "Mexican Defense": "Мексиканская защита",
    "Mieses Opening": "Дебют Мизеса",
    "Mikenas Defense": "Защита Микенаса",
    "Modern Defense": "Современная защита",
    "Montevideo Defense": "Защита Монтевидео",
    "Neo-Grünfeld Defense": "Защита Нео-Грюнфельда",
    "Nimzo-Indian Defense": "Защита Нимцовича",
    "Nimzo-Larsen Attack": "Дебют Нимцовича — Ларсена",
    "Nimzowitsch Defense": "Защита Нимцовича",
    "Old Indian Defense": "Староиндийская защита",
    "Owen Defense": "Защита Оуэна",
    "Paleface Attack": "Атака бледнолицего",
    "Petrov's Defense": "Русская партия",
    "Philidor Defense": "Защита Филидора",
    "Pirc Defense": "Защита Пирца — Уфимцева",
    "Polish Defense": "Польская защита",
    "Polish Opening": "Дебют Сокольского",
    "Polish Opening, with d5": "Дебют Сокольского",
    "Ponziani Opening": "Дебют Понциани",
    "Portuguese Opening": "Португальский дебют",
    "Pseudo Queen's Indian Defense": "Псевдоновоиндийская защита",
    "Pterodactyl Defense": "Защита птеродактиля",
    "Queen's Gambit": "Ферзевый гамбит",
    "Queen's Gambit Accepted": "Принятый ферзевый гамбит",
    "Queen's Gambit Declined": "Отказанный ферзевый гамбит",
    "Queen's Indian Accelerated": "Новоиндийская защита",
    "Queen's Indian Defense": "Новоиндийская защита",
    "Queen's Indian Defense, with e3": "Новоиндийская защита",
    "Queen's Indian Defense, with e3, Bb4+ Line": "Новоиндийская защита",
    "Queen's Pawn Game": "Дебют ферзевой пешки",
    "Queen's Pawn, Mengarini Attack": "Дебют ферзевой пешки",
    "Rapport-Jobava System": "Система Раппорта — Джобавы",
    "Rapport-Jobava System, with e6": "Система Раппорта — Джобавы",
    "Rat Defense": "Защита крысы",
    "Richter-Veresov Attack": "Атака Рихтера — Вересова",
    "Robatsch Defense": "Защита Робача",
    "Rubinstein Opening": "Дебют Рубинштейна",
    "Réti Opening": "Дебют Рети",
    "Ruy Lopez": "Испанская партия",
    "Saragossa Opening": "Сарагосский дебют",
    "Scandinavian Defense": "Скандинавская защита",
    "Scotch Game": "Шотландская партия",
    "Semi-Slav Defense": "Полуславянская защита",
    "Semi-Slav Defense Accepted": "Принятая полуславянская защита",
    "Sicilian Defense": "Сицилианская защита",
    "Slav Defense": "Славянская защита",
    "Slav Indian": "Славянско-индийская защита",
    "Sodium Attack": "Натриевая атака",
    "St. George Defense": "Защита Святого Георгия",
    "Tarrasch Defense": "Защита Тарраша",
    "Three Knights Opening": "Дебют трёх коней",
    "Torre Attack": "Атака Торре",
    "Trompowsky Attack": "Атака Тромповского",
    "Valencia Opening": "Валенсийский дебют",
    "Van Geet Opening": "Дебют ван Гета",
    "Van't Kruijs Opening": "Дебют ван Крейса",
    "Vienna Game": "Венская партия",
    "Vienna Gambit, with Max Lange Defense": "Венский гамбит",
    "Vulture Defense": "Защита грифа",
    "Wade Defense": "Защита Уэйда",
    "Ware Defense": "Защита Уэра",
    "Ware Opening": "Дебют Уэра",
    "Yusupov-Rubinstein System": "Система Юсупова — Рубинштейна",
    "Zaire Defense": "Заирская защита",
    "Zukertort Defense": "Защита Цукерторта",
    "Zukertort Opening": "Дебют Цукерторта",
}

# Only the variations a player is likely to reach and that have a settled Russian
# name. The catalogue holds nearly three thousand distinct variation strings, most
# of them English move descriptions; an unmapped one is dropped rather than
# transliterated, so the family name alone is spoken.
_RUSSIAN_VARIATIONS: dict[str, str] = {
    "Advance Variation": "вариант с продвижением",
    "Chigorin Defense": "защита Чигорина",
    "Chigorin Variation": "вариант Чигорина",
    "Classical Defense": "классическая защита",
    "Classical Variation": "классический вариант",
    "Closed": "закрытый вариант",
    "Colle System": "система Колле",
    "Dragon Variation": "вариант дракона",
    "Exchange Variation": "разменный вариант",
    "Fianchetto Variation": "вариант с фианкетто",
    "Keres Variation": "вариант Кереса",
    "Kádas Gambit": "гамбит Кадаша",
    "London System": "лондонская система",
    "Main Line": "главный вариант",
    "Modern Defense": "современная защита",
    "Modern Variation": "современный вариант",
    "Morphy Defense": "защита Морфи",
    "Najdorf Variation": "вариант Найдорфа",
    "Normal Variation": "нормальный вариант",
    # Said after «Защита Бенони», so the family name is not repeated.
    "Old Benoni": "старый вариант",
    "Open": "открытый вариант",
    "Open Defense": "открытая защита",
    "Pachman Gambit": "гамбит Пахмана",
    "Panov Attack": "атака Панова",
    "Retreat Variation": "вариант с отступлением",
    "Semi-Tarrasch Defense": "улучшенная защита Тарраша",
    "Sicilian Variation": "сицилианский вариант",
    "Smyslov Variation": "вариант Смыслова",
    "St. Petersburg Variation": "петербургский вариант",
    "Steinitz Variation": "вариант Стейница",
    "Stoltz Variation": "вариант Штольца",
    "Stonewall Variation": "вариант «каменная стена»",
    "Symmetrical Variation": "симметричный вариант",
    "Sämisch Variation": "вариант Земиша",
    "Taimanov Variation": "вариант Тайманова",
    "Tarrasch Defense": "защита Тарраша",
    "Three Knights Variation": "вариант трёх коней",
    "Torre Attack": "атака Торре",
    "Two Knights Variation": "вариант двух коней",
    "Winawer Variation": "вариант Винавера",
    "Wing Gambit": "фланговый гамбит",
    "Zilbermints Gambit": "гамбит Зильберминца",
}


@cache
def _opening_index() -> dict[tuple[str, ...], OpeningName]:
    """UCI prefix → opening, loaded once from the packaged import."""
    resource = files(_OPENINGS_RESOURCE[0]).joinpath(*_OPENINGS_RESOURCE[1:])
    reader = csv.DictReader(resource.read_text(encoding="utf-8").splitlines(), delimiter="\t")
    return {tuple(row["uci"].split()): OpeningName(row["eco"], row["opening"], row["variation"]) for row in reader}


def identify_opening(board: chess.Board) -> OpeningName | None:
    """The longest known ECO line the game still starts with, if there is one."""
    if board.root() != chess.Board():
        return None
    moves = tuple(move.uci() for move in board.move_stack)
    index = _opening_index()
    for length in range(len(moves), 0, -1):
        known = index.get(moves[:length])
        if known is not None:
            return known
    return None


def game_stage(board: chess.Board) -> GameStage:
    """Which stage the position is in, by material first and development second.

    Material decides the endgame on its own: a position traded down to rooks and
    a minor piece is an endgame however early it happened.
    """
    if all(_material(board, colour) <= _ENDGAME_MATERIAL for colour in chess.COLORS):
        return GameStage.ENDGAME
    if len(board.move_stack) < _OPENING_PLIES and _undeveloped_minors(board) >= _UNDEVELOPED_MINORS:
        return GameStage.OPENING
    return GameStage.MIDDLEGAME


def russian_name(known: OpeningName) -> str | None:
    """The spoken Russian name of `known`, or `None` when nothing may be spoken."""
    family = _RUSSIAN_OPENINGS.get(known.opening)
    if family is None:
        return None
    variation = _RUSSIAN_VARIATIONS.get(known.variation)
    return f"{family}, {variation}" if variation else family


def describe_opening(board: chess.Board) -> Speech:
    known = identify_opening(board)
    if known is None:
        return Speech.of("Дебют не определён.")
    translated = spoken_opening_name(known)
    if translated is None:
        return Speech.of(f"Дебют определён по коду {known.eco}; русского названия в справочнике пока нет.")
    return Speech.of(f"Это {translated}, код {known.eco}.")


def spoken_opening_name(name: OpeningName) -> str | None:
    """The Russian name as it is said inside a sentence: «это испанская партия»."""
    translated = russian_name(name)
    if translated is None:
        return None
    return translated[0].lower() + translated[1:]


def describe_stage(board: chess.Board) -> Speech:
    return Speech.of(f"Сейчас {_STAGE_NAMES[game_stage(board)]}.")


def _material(board: chess.Board, colour: chess.Color) -> int:
    return sum(_PIECE_VALUES[piece_type] * len(board.pieces(piece_type, colour)) for piece_type in chess.PIECE_TYPES)


def _undeveloped_minors(board: chess.Board) -> int:
    return sum(
        1
        for square, colour, piece_type in _MINOR_HOME_SQUARES
        if board.piece_at(square) == chess.Piece(piece_type, colour)
    )
