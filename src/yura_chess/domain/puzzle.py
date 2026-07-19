"""Tactical puzzles: the offline catalogue and one player's progress through it.

A puzzle owns no game. It is a position plus the forced line that solves it, and
an attempt walks that line one node at a time; nothing here can touch a game row,
a revision or a pending engine turn.

The catalogue is the offline CC0 import in `yura_chess/data/puzzles.jsonl`, read
once into memory the way the ECO set is — runtime never reaches for the source
database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cache
from importlib.resources import files

__all__ = [
    "CLEAN_SOLVES_TO_PROMOTE",
    "DEFAULT_PUZZLE_BUCKET",
    "FAILURES_TO_DEMOTE",
    "Puzzle",
    "PuzzleAttempt",
    "PuzzleAttemptStatus",
    "PuzzleBucket",
    "PuzzleProfile",
    "bucket_for_rating",
    "catalogue",
]

_PUZZLES_RESOURCE = ("yura_chess", "data", "puzzles.jsonl")

# The bucket boundaries the importer already applied to the shipped catalogue.
_LOW_MAX_RATING = 1400
_MEDIUM_MAX_RATING = 1800

# How steady the player has to be before the difficulty follows them.
CLEAN_SOLVES_TO_PROMOTE = 3
FAILURES_TO_DEMOTE = 2


class PuzzleBucket(StrEnum):
    """Difficulty band, ordered from easiest to hardest."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    def harder(self) -> PuzzleBucket:
        """One band up, staying inside the catalogue."""
        return _BUCKET_ORDER[min(_BUCKET_ORDER.index(self) + 1, len(_BUCKET_ORDER) - 1)]

    def easier(self) -> PuzzleBucket:
        """One band down, staying inside the catalogue."""
        return _BUCKET_ORDER[max(_BUCKET_ORDER.index(self) - 1, 0)]


_BUCKET_ORDER: tuple[PuzzleBucket, ...] = (PuzzleBucket.LOW, PuzzleBucket.MEDIUM, PuzzleBucket.HIGH)

DEFAULT_PUZZLE_BUCKET = PuzzleBucket.MEDIUM


def bucket_for_rating(rating: int) -> PuzzleBucket:
    """Which band a Lichess rating falls into."""
    if rating <= _LOW_MAX_RATING:
        return PuzzleBucket.LOW
    if rating <= _MEDIUM_MAX_RATING:
        return PuzzleBucket.MEDIUM
    return PuzzleBucket.HIGH


class PuzzleAttemptStatus(StrEnum):
    """How an attempt ended, or that it has not ended yet."""

    ACTIVE = "active"
    SOLVED = "solved"
    FAILED = "failed"
    ABANDONED = "abandoned"

    @property
    def is_finished(self) -> bool:
        return self is not PuzzleAttemptStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class Puzzle:
    """One catalogue entry.

    `moves` is the solution line as the source records it: the opponent's move
    that creates the position is first, and player and opponent alternate from
    there, so every even index is a forced reply and every odd one is a move the
    player has to find.
    """

    id: str
    fen: str
    moves: tuple[str, ...]
    rating: int
    themes: tuple[str, ...]
    bucket: PuzzleBucket


@dataclass(frozen=True, slots=True)
class PuzzleProfile:
    """How hard this owner's puzzles are, and how steadily they are going.

    The two streaks are counted separately and never both run: a clean solve
    clears the failures, a failure clears the clean solves.
    """

    owner_key: str
    bucket: PuzzleBucket = DEFAULT_PUZZLE_BUCKET
    clean_streak: int = 0
    failure_streak: int = 0


@dataclass(frozen=True, slots=True)
class PuzzleAttempt:
    """One owner working through one puzzle.

    `node` is an absolute index into the puzzle's solution line, so re-applying a
    request that advanced it leaves it exactly where it already is. `streak` is
    the clean run the owner was on when the attempt finished — a snapshot, so a
    replayed answer reports the same series it reported the first time.
    """

    owner_key: str
    puzzle_id: str
    node: int
    mistakes: int
    hints: int
    streak: int
    status: PuzzleAttemptStatus
    revision: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


@cache
def catalogue() -> tuple[Puzzle, ...]:
    """The shipped puzzles, in the order the importer wrote them."""
    resource = files(_PUZZLES_RESOURCE[0]).joinpath(*_PUZZLES_RESOURCE[1:])
    return tuple(
        Puzzle(
            id=entry["id"],
            fen=entry["fen"],
            moves=tuple(entry["moves"]),
            rating=entry["rating"],
            themes=tuple(entry["themes"]),
            bucket=PuzzleBucket(entry["bucket"]),
        )
        for entry in (json.loads(line) for line in resource.read_text(encoding="utf-8").splitlines() if line)
    )
