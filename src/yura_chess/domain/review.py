"""Where the player has got to while going through a finished game.

A review owns no chess: the game it walks is finished and immutable, and the
cursor only remembers which section is being read and how far into it the voice
has come. Everything spoken during a review is derived from the canonical UCI
history and the stored analysis checkpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = [
    "DEFAULT_REVIEW_SECTION",
    "GameReview",
    "ReviewSection",
]


class ReviewSection(StrEnum):
    """The parts of a review, in the order the product plan names them."""

    SUMMARY = "summary"
    TURNING_POINT = "turning_point"
    MISTAKES = "mistakes"
    MOVES = "moves"


DEFAULT_REVIEW_SECTION = ReviewSection.SUMMARY


@dataclass(frozen=True, slots=True)
class GameReview:
    """One owner's position inside the review of one finished game.

    `ply` points at the move being discussed, `page` at the page of the current
    section; both are absolute, so re-applying a request that moved the cursor
    leaves it exactly where it already is.
    """

    game_id: str
    owner_key: str
    section: ReviewSection
    ply: int
    page: int
    revision: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
