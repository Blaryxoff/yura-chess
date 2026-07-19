"""Read-only engine analysis of a position.

Analysis never belongs to a game row: it values one board copy and returns the
moves the engine likes, best first. Scores are stored the way the engine reports
them — from the side to move — and are turned to a player's point of view only
when they are spoken.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

import chess
import chess.engine

from yura_chess.domain.game import PlayerColor

# A mate outranks any material advantage; the distance only orders mates.
MATE_CENTIPAWNS = 10_000
_MAX_MATE_DISTANCE = 99

# The single thresholds of the whole skill, in centipawns lost by the player.
INACCURACY_CENTIPAWNS = 50
MISTAKE_CENTIPAWNS = 100
BLUNDER_CENTIPAWNS = 200

POSITION_HASH_LENGTH = 64


@dataclass(frozen=True, slots=True)
class Score:
    """Position value from the perspective of one side.

    Exactly one of the two fields is set: `centipawns` for a normal evaluation,
    `mate_in` for a forced mate, positive when that side delivers it.
    """

    centipawns: int | None = None
    mate_in: int | None = None

    def __post_init__(self) -> None:
        if (self.centipawns is None) == (self.mate_in is None):
            raise ValueError("a score is either centipawns or a mate distance")
        if self.mate_in == 0:
            raise ValueError("a mate distance is never zero")

    @property
    def is_mate(self) -> bool:
        return self.mate_in is not None

    def as_centipawns(self) -> int:
        """A single comparable number; mates saturate above any material score."""
        mate = self.mate_in
        if mate is None:
            return self.centipawns or 0
        distance = min(abs(mate), _MAX_MATE_DISTANCE)
        value = MATE_CENTIPAWNS - distance
        return value if mate > 0 else -value

    def inverted(self) -> Score:
        """The same value seen by the opponent."""
        mate = self.mate_in
        if mate is not None:
            return Score(mate_in=-mate)
        return Score(centipawns=-(self.centipawns or 0))


@dataclass(frozen=True, slots=True)
class MoveCandidate:
    """One move the engine considered, with the line it expects after it."""

    move: str
    score: Score
    principal_variation: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PositionAnalysis:
    """What the engine thinks of one position, candidates ordered best first."""

    fen: str
    side_to_move: PlayerColor
    depth: int
    candidates: tuple[MoveCandidate, ...] = ()

    @property
    def best(self) -> MoveCandidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def score(self) -> Score | None:
        """The position's value for the side to move."""
        return self.candidates[0].score if self.candidates else None

    def score_for(self, color: PlayerColor) -> Score | None:
        """The position's value for one player, whoever is to move."""
        score = self.score
        if score is None:
            return None
        return score if color is self.side_to_move else score.inverted()


class MoveQuality(StrEnum):
    """How much a played move cost its player, by the fixed skill thresholds."""

    GOOD = "good"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


def classify_loss(centipawn_loss: int) -> MoveQuality:
    """Name a loss the player suffered; the thresholds include their boundary.

    Losing or allowing a forced mate needs no special case: a mate saturates far
    above `BLUNDER_CENTIPAWNS`, so turning one into a normal position — or a
    normal position into one — always exceeds the blunder threshold, while a mate
    merely postponed stays a good move.
    """
    if centipawn_loss >= BLUNDER_CENTIPAWNS:
        return MoveQuality.BLUNDER
    if centipawn_loss >= MISTAKE_CENTIPAWNS:
        return MoveQuality.MISTAKE
    if centipawn_loss >= INACCURACY_CENTIPAWNS:
        return MoveQuality.INACCURACY
    return MoveQuality.GOOD


def position_hash(fen: str) -> str:
    """Identify the position a checkpoint values, independent of how it is drawn."""
    return sha256(fen.encode("utf-8")).hexdigest()[:POSITION_HASH_LENGTH]


@dataclass(frozen=True, slots=True)
class AnalysisEngineSettings:
    """What the search that produced a checkpoint was allowed to do.

    Kept with the checkpoint so a later review can decide whether the stored
    verdict is good enough to reuse or has to be recomputed.
    """

    depth: int
    search_time_ms: int
    skill_level: int


@dataclass(frozen=True, slots=True)
class AnalysisCheckpoint:
    """One valued player move of a game.

    `position_hash` covers the position *before* the move, the one `score_before`
    values. Both scores are seen by the player who moved, so a positive
    `centipawn_loss` always means that player lost value.
    """

    game_id: str
    owner_key: str
    ply: int
    position_hash: str
    score_before: Score
    score_after: Score
    centipawn_loss: int
    engine: AnalysisEngineSettings

    @property
    def quality(self) -> MoveQuality:
        return classify_loss(self.centipawn_loss)


def centipawn_loss(score_before: Score, score_after: Score) -> int:
    """How much the moving player lost, both scores seen from their side."""
    return score_before.as_centipawns() - score_after.as_centipawns()


def score_from_engine(score: chess.engine.Score) -> Score:
    """Convert one `python-chess` relative score into the domain score."""
    mate = score.mate()
    if mate is not None:
        return Score(mate_in=mate)
    return Score(centipawns=score.score(mate_score=MATE_CENTIPAWNS))


def analysis_from_info(board: chess.Board, infos: list[Any]) -> PositionAnalysis:
    """Build the analysis of `board` from the engine's multipv info dicts.

    Lines without a principal variation carry no move to recommend and are
    dropped; a position with no legal move leaves the candidates empty.
    """
    side_to_move = PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK
    candidates: list[MoveCandidate] = []
    depth = 0
    for info in infos:
        depth = max(depth, int(info.get("depth", 0)))
        variation = tuple(move.uci() for move in info.get("pv", ()))
        pov_score = info.get("score")
        if not variation or pov_score is None:
            continue
        candidates.append(
            MoveCandidate(
                move=variation[0],
                score=score_from_engine(pov_score.relative),
                principal_variation=variation,
            )
        )
    candidates.sort(key=lambda candidate: candidate.score.as_centipawns(), reverse=True)
    return PositionAnalysis(
        fen=board.fen(),
        side_to_move=side_to_move,
        depth=depth,
        candidates=tuple(candidates),
    )
