"""Russian speech → a canonical token signature.

The maps below cover morphology and the standard Russian pronunciation of the
files, not invented synonyms: a wrong guess here silently changes which move the
skill plays. Anything unrecognised becomes an unknown word rather than an error,
because the resolver still has the legal moves of the position to match against.
"""

from __future__ import annotations

import re

from yura_chess.voice.types import Normalized, Signature, Token, TokenKind

MAX_UTTERANCE_LENGTH = 512
MAX_UNKNOWN_WORDS = 32

_PIECES: dict[str, str] = {
    "пешка": "P",
    "пешку": "P",
    "пешки": "P",
    "пешкой": "P",
    "пешке": "P",
    "конь": "N",
    "коня": "N",
    "коню": "N",
    "конем": "N",
    "конь-": "N",
    "кони": "N",
    "слон": "B",
    "слона": "B",
    "слону": "B",
    "слоном": "B",
    "слоны": "B",
    "ладья": "R",
    "ладью": "R",
    "ладьи": "R",
    "ладьей": "R",
    "ладье": "R",
    "ферзь": "Q",
    "ферзя": "Q",
    "ферзю": "Q",
    "ферзем": "Q",
    "королева": "Q",
    "королеву": "Q",
    "королевой": "Q",
    "король": "K",
    "короля": "K",
    "королю": "K",
    "королем": "K",
}

_RANKS: dict[str, str] = {
    "1": "1",
    "один": "1",
    "одна": "1",
    "первая": "1",
    "2": "2",
    "два": "2",
    "две": "2",
    "вторая": "2",
    "3": "3",
    "три": "3",
    "третья": "3",
    "4": "4",
    "четыре": "4",
    "четвертая": "4",
    "5": "5",
    "пять": "5",
    "пятая": "5",
    "6": "6",
    "шесть": "6",
    "шестая": "6",
    "7": "7",
    "семь": "7",
    "седьмая": "7",
    "8": "8",
    "восемь": "8",
    "восьмая": "8",
}

# Words that can only be a file letter.
_FILES_STRICT: dict[str, str] = {
    "эй": "a",
    "бэ": "b",
    "бе": "b",
    "би": "b",
    "эс": "c",
    "це": "c",
    "цэ": "c",
    "си": "c",
    "дэ": "d",
    "де": "d",
    "ди": "d",
    "эф": "f",
    "гэ": "g",
    "жэ": "g",
    "джи": "g",
    "аш": "h",
    "ха": "h",
    "эйч": "h",
}

# Words that are a file letter only when a rank follows; otherwise they are the
# Russian prepositions and conjunctions that surround a spoken move.
_FILES_WEAK: dict[str, str] = {
    "а": "a",
    "б": "b",
    "с": "c",
    "ц": "c",
    "д": "d",
    "е": "e",
    "э": "e",
    "и": "e",
    "ф": "f",
    "г": "g",
    "ж": "g",
    "же": "g",
    "х": "h",
    # Yandex ASR returns coordinates in Latin as often as in Cyrillic.
    "a": "a",
    "b": "b",
    "c": "c",
    "d": "d",
    "e": "e",
    "f": "f",
    "g": "g",
    "h": "h",
}

# These spell ordinary Russian function words so often that treating them as a
# source-file hint without a following rank risks playing the wrong move.
_FUNCTION_WORD_FILES = frozenset({"а", "с", "е", "и"})

_CAPTURES = frozenset(
    {"бьет", "бей", "бьем", "берет", "бери", "взять", "взял", "бьют", "съесть", "съел", "руби", "рубит"}
)

_PROMOTIONS = frozenset({"превращение", "превращаю", "превратить", "превращается", "становится", "ставлю"})

# Filler that carries no move information; unlike unknown words it costs no confidence.
_FILLER = frozenset(
    {
        "на",
        "в",
        "во",
        "из",
        "со",
        "до",
        "к",
        "ко",
        "по",
        "ход",
        "ходи",
        "ходить",
        "ходом",
        "иди",
        "идет",
        "пойди",
        "походи",
        "давай",
        "сделай",
        "пожалуйста",
        "теперь",
        "мой",
        "моя",
        "мою",
        "моим",
        "свой",
        "мне",
        "я",
        "ты",
        "поле",
        "клетку",
    }
)

_CASTLE = re.compile(r"рокировк")
# Matched per word rather than across the utterance: the bare "больш" stem also
# opens "большое спасибо", and a stray match castles to the side nobody asked for.
# Only the feminine forms agree with "рокировка".
_CASTLE_LONG = re.compile(r"^(?:длинн|ферзев)|^больш(?:ая|ую|ой)$")
# Letters and digits are separate runs, so ASR output glued as "е4" or "e4" still
# tokenises into a file and a rank instead of one unrecognised word.
_WORD = re.compile(r"[а-я]+|[a-z]+|[0-9]+")


def normalize(text: str) -> Normalized:
    """Reduce an utterance to lowercase words and a move signature."""
    lowered = text[:MAX_UTTERANCE_LENGTH].lower().replace("ё", "е").replace("-", " ")
    words = tuple(_WORD.findall(lowered))
    signature, unknown = _tokenize(words, lowered)
    return Normalized(text=" ".join(words), words=words, signature=signature, unknown_words=unknown)


def _tokenize(words: tuple[str, ...], lowered: str) -> tuple[Signature, tuple[str, ...]]:
    if _CASTLE.search(lowered):
        long_side = any(
            _CASTLE_LONG.match(word) and (index == 0 or words[index - 1] != "не") for index, word in enumerate(words)
        )
        kind = TokenKind.CASTLE_LONG if long_side else TokenKind.CASTLE_SHORT
        return (Token(kind),), ()

    tokens: list[Token] = []
    unknown: list[str] = []
    promotion_announced = False
    for index, word in enumerate(words):
        if word in _PIECES:
            tokens.append(Token(TokenKind.PIECE, _PIECES[word]))
        elif word in _RANKS:
            tokens.append(Token(TokenKind.RANK, _RANKS[word]))
        elif word in _FILES_STRICT:
            tokens.append(Token(TokenKind.FILE, _FILES_STRICT[word]))
        elif word in _FILES_WEAK:
            # A bare "с" or "а" is a preposition; followed by a rank it is a file.
            following = words[index + 1] if index + 1 < len(words) else None
            followed_by_rank = following in _RANKS
            followed_by_file = following in _FILES_STRICT or following in _FILES_WEAK
            if followed_by_rank or (followed_by_file and word not in _FUNCTION_WORD_FILES):
                tokens.append(Token(TokenKind.FILE, _FILES_WEAK[word]))
            # Otherwise it is the preposition or conjunction it also spells;
            # dropping it silently keeps confidence intact.
        elif word in _CAPTURES:
            tokens.append(Token(TokenKind.CAPTURE))
        elif word in _PROMOTIONS:
            promotion_announced = True
        elif word not in _FILLER:
            if len(unknown) < MAX_UNKNOWN_WORDS:
                unknown.append(word)

    merged = _merge_squares(tokens)
    return _mark_promotion(merged, promotion_announced), tuple(unknown)


def _merge_squares(tokens: list[Token]) -> list[Token]:
    merged: list[Token] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if token.kind is TokenKind.FILE and following is not None and following.kind is TokenKind.RANK:
            merged.append(Token(TokenKind.SQUARE, token.value + following.value))
            index += 2
            continue
        merged.append(token)
        index += 1
    return merged


def _mark_promotion(tokens: list[Token], announced: bool) -> Signature:
    """A piece named after the destination square is the promotion piece."""
    if not tokens or tokens[-1].kind is not TokenKind.PIECE:
        return tuple(tokens)
    if not announced and not any(token.kind is TokenKind.SQUARE for token in tokens[:-1]):
        return tuple(tokens)
    if tokens[-1].value not in {"Q", "R", "B", "N"}:
        return tuple(tokens)
    return (*tokens[:-1], Token(TokenKind.PROMOTION, tokens[-1].value.lower()))
