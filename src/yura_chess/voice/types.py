"""Shared vocabulary of the voice layer.

A spoken move is reduced to a *signature*: the ordered sequence of significant
tokens left after normalisation. The resolver builds the same kind of signature
for every legal move of the current position and matches the two, so ASR noise
is corrected by the position rather than by a synonym dictionary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TokenKind(StrEnum):
    PIECE = "piece"
    SQUARE = "square"
    FILE = "file"
    RANK = "rank"
    CAPTURE = "capture"
    CASTLE_SHORT = "castle_short"
    CASTLE_LONG = "castle_long"
    PROMOTION = "promotion"


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    # Piece letter, square name, file letter or rank digit; empty for flag tokens.
    value: str = ""


Signature = tuple[Token, ...]


@dataclass(frozen=True, slots=True)
class Normalized:
    """The privacy-safe view of an utterance: no raw payload, no identifiers."""

    text: str
    words: tuple[str, ...]
    signature: Signature
    # Words that matched nothing known; they lower confidence but never block a match.
    unknown_words: tuple[str, ...] = ()

    @property
    def has_move_tokens(self) -> bool:
        return bool(self.signature)


@dataclass(frozen=True, slots=True)
class RecognizedMove:
    """What the utterance said about a move, whether or not it is legal.

    Kept even for an unmatched utterance: the illegal-move explainer works from
    exactly these fields.
    """

    piece: str | None = None
    source: str | None = None
    source_file: str | None = None
    source_rank: str | None = None
    destination: str | None = None
    promotion: str | None = None
    capture: bool = False
    castle_short: bool = False
    castle_long: bool = False

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.piece,
                self.source,
                self.source_file,
                self.source_rank,
                self.destination,
                self.promotion,
                self.castle_short,
                self.castle_long,
            )
        )


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


@dataclass(frozen=True, slots=True)
class MoveResolution:
    status: ResolutionStatus
    # Confidence of the reading, not of the move's quality; 0.0 unless resolved.
    confidence: float = 0.0
    move: str | None = None
    candidates: tuple[str, ...] = ()
    recognized: RecognizedMove = field(default_factory=RecognizedMove)
