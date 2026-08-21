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
    "лошадь": "N",
    "лошади": "N",
    "лошадью": "N",
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

# Yandex ASR writes the final /f/ of «конь эф» the way Russian spells that sound
# at the end of a word, gluing the piece and the file into «конев». Only a rank
# right after it makes the word a move; on its own it is a surname.
_GLUED_PIECE_FILES: dict[str, tuple[str, str]] = {"конев": ("N", "f")}

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

# A glued number right after one of these spells a count far more often than a
# square: «мне б 24» is not a move. Wider than the set above, because «конь б е
# пять» still names the b-file knight — only the glued form is doubtful.
_GLUE_AMBIGUOUS_FILES = _FUNCTION_WORD_FILES | {"б", "ж", "же"}

_CAPTURES = frozenset(
    {"бьет", "бей", "бьем", "берет", "бери", "взять", "взял", "бьют", "съесть", "съел", "руби", "рубит"}
)

_PROMOTIONS = frozenset({"превращение", "превращаю", "превратить", "превращается", "становится", "ставлю"})

# A promotion piece can be named without the word «превращение», but only in
# the nominative/accusative and only on the last rank. Instrumental forms such
# as «c5 d3 конем» describe the moving piece, not a promotion.
_IMPLICIT_PROMOTION_PIECES = frozenset({"ферзь", "ферзя", "ладья", "ладью", "слон", "слона", "конь", "коня"})

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

_CASTLE = re.compile(r"рокир")
_CASTLE_NOTATION = re.compile(r"(?<!\w)(?:0|о|o)\s*(?:-|тире)?\s*(?:0|о|o)(?:\s*(?:-|тире)?\s*(?:0|о|o))?(?!\w)")
# Matched per word rather than across the utterance: the bare "больш" stem also
# opens "большое спасибо", and a stray match castles to the side nobody asked for.
# Only the feminine forms agree with "рокировка".
_CASTLE_LONG = re.compile(r"^(?:длинн|ферзев)|^больш(?:ая|ую|ой)$")
# A frame that mentions castling instead of asking for it.
_CASTLE_MENTIONED = re.compile(
    r"\bне (?:хочу|надо|буду|нужно|стоит)\b|\bчто такое\b|\bэто что\b|\bвместо\b|\bбез\b|\bотмени\w*\b"
)
# Letters and digits are separate runs, so ASR output glued as "е4" or "e4" still
# tokenises into a file and a rank instead of one unrecognised word.
_WORD = re.compile(r"[а-я]+|[a-z]+|[0-9]+")
# ASR swallows the destination file of a spoken move often enough that the two
# ranks arrive as one number: «ферзь дэ два цэ три» comes back as «ферзь д 23».
# Split only a pair of board ranks named right after a file, so that «уровень 12»
# and «мне 65 лет» stay the numbers they are.
_GLUED_RANKS = re.compile(r"^[1-8]{2}$")


def normalize(text: str) -> Normalized:
    """Reduce an utterance to lowercase words and a move signature."""
    raw_lowered = text[:MAX_UTTERANCE_LENGTH].lower().replace("ё", "е")
    lowered = raw_lowered.replace("-", " ")
    words = tuple(_WORD.findall(lowered))
    signature, unknown = _tokenize(words, raw_lowered)
    return Normalized(text=" ".join(words), words=words, signature=signature, unknown_words=unknown)


def _notation_meant(match: str, lowered: str) -> bool:
    """A separator makes «0-0» notation anywhere; a spaced «0 0» only alone, so «счет 0 0» stays a score."""
    if "-" in match or "тире" in match:
        return True
    return match.strip() == lowered.strip()


def _tokenize(words: tuple[str, ...], lowered: str) -> tuple[Signature, tuple[str, ...]]:
    words, recovered = _split_glued_ranks(words)
    notation = _CASTLE_NOTATION.search(lowered)
    if notation is not None and _notation_meant(notation.group(), lowered):
        marker_count = sum(character in "0оo" for character in notation.group())
        kind = TokenKind.CASTLE_LONG if marker_count >= 3 else TokenKind.CASTLE_SHORT
        return (Token(kind),), ()

    castle_word = max((index for index, word in enumerate(words) if _CASTLE.search(word)), default=-1)
    suffix = words[castle_word + 1 :]
    later_square = any(
        word in _FILES_STRICT | _FILES_WEAK and index + 1 < len(suffix) and suffix[index + 1] in _RANKS
        for index, word in enumerate(suffix)
    )
    if castle_word >= 0 and not later_square and not _CASTLE_MENTIONED.search(lowered):
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
        elif word in _GLUED_PIECE_FILES and index + 1 < len(words) and words[index + 1] in _RANKS:
            piece, file = _GLUED_PIECE_FILES[word]
            tokens.append(Token(TokenKind.PIECE, piece))
            tokens.append(Token(TokenKind.FILE, file))
        elif word in _RANKS:
            kind = TokenKind.DESTINATION_RANK if index in recovered else TokenKind.RANK
            tokens.append(Token(kind, _RANKS[word]))
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
    spoken = [word for word in words if word not in _FILLER]
    implicit_promotion = bool(spoken and spoken[-1] in _IMPLICIT_PROMOTION_PIECES and _final_rank(merged) in {"1", "8"})
    return _mark_promotion(merged, promotion_announced or implicit_promotion), tuple(unknown)


def _split_glued_ranks(words: tuple[str, ...]) -> tuple[tuple[str, ...], frozenset[int]]:
    """The words with glued rank pairs split, and where the second half landed.

    That second half is the rank the move ends on, and only it: a rank spoken
    again on its own — «пешка е четыре, повторяю, четыре» — is an echo.
    """
    # A doubtful file needs a piece named somewhere for the number after it to
    # become a square: «конь а 23» is a move and «и 24» is not.
    named_piece = any(word in _PIECES for word in words)
    split: list[str] = []
    recovered: set[int] = set()
    for index, word in enumerate(words):
        previous = words[index - 1] if index else ""
        after_file = previous in _FILES_STRICT | _FILES_WEAK and (named_piece or previous not in _GLUE_AMBIGUOUS_FILES)
        if after_file and _GLUED_RANKS.match(word):
            split.extend(word)
            recovered.add(len(split) - 1)
        else:
            split.append(word)
    return tuple(split), frozenset(recovered)


def _final_rank(tokens: list[Token]) -> str | None:
    """The rank the move ends on; a bare rank stands in for a file ASR swallowed."""
    for token in reversed(tokens):
        if token.kind is TokenKind.RANK or token.kind is TokenKind.DESTINATION_RANK:
            return token.value
        if token.kind is TokenKind.SQUARE:
            return token.value[1]
    return None


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
    if not announced:
        return tuple(tokens)
    if not tokens or tokens[-1].kind is not TokenKind.PIECE:
        return tuple(tokens)
    if not any(token.kind is TokenKind.SQUARE for token in tokens[:-1]):
        return tuple(tokens)
    if tokens[-1].value not in {"Q", "R", "B", "N"}:
        return tuple(tokens)
    return (*tokens[:-1], Token(TokenKind.PROMOTION, tokens[-1].value.lower()))
