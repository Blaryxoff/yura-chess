"""Owner-scoped storage for the valued player moves of a game.

A checkpoint is a cache of a read-only analysis, never a fact the game depends
on: a missing one is normal and simply means the position has to be analysed
again. Writing is an upsert on (game, ply), so a re-applied request rewrites the
same row with the same values instead of adding a second verdict for one move.

Reading requires the owner key, so a foreign or forged game id yields nothing
rather than another player's analysis.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yura_chess.domain.analysis import (
    AnalysisCheckpoint,
    AnalysisEngineSettings,
    Score,
)
from yura_chess.storage.models import AnalysisCheckpointRow, GameRow


class UnknownCheckpointGameError(LookupError):
    """No game with this id belongs to this owner, so it cannot be valued."""


def _to_score(centipawns: int | None, mate_in: int | None) -> Score:
    return Score(centipawns=centipawns, mate_in=mate_in)


def _to_checkpoint(row: AnalysisCheckpointRow) -> AnalysisCheckpoint:
    return AnalysisCheckpoint(
        game_id=row.game_id,
        owner_key=row.owner_key,
        ply=row.ply,
        position_hash=row.position_hash,
        score_before=_to_score(row.score_before_centipawns, row.score_before_mate_in),
        score_after=_to_score(row.score_after_centipawns, row.score_after_mate_in),
        centipawn_loss=row.centipawn_loss,
        engine=AnalysisEngineSettings(
            depth=row.engine_depth,
            search_time_ms=row.engine_search_time_ms,
            skill_level=row.engine_skill_level,
        ),
    )


class AnalysisRepository:
    """Thin data-access layer bound to one short transaction (one Session)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, checkpoint: AnalysisCheckpoint) -> AnalysisCheckpoint:
        """Store the valuation of one player move, replacing any earlier one."""
        self._require_own_game(checkpoint.game_id, checkpoint.owner_key)
        row = self._find_row(checkpoint.game_id, checkpoint.owner_key, checkpoint.ply)
        if row is None:
            row = self._insert(checkpoint)
        else:
            self._apply(row, checkpoint)
        self._session.flush()
        return _to_checkpoint(row)

    def find(self, game_id: str, owner_key: str, ply: int) -> AnalysisCheckpoint | None:
        """A position that was never valued is not an error."""
        row = self._find_row(game_id, owner_key, ply)
        return _to_checkpoint(row) if row is not None else None

    def list_for_game(self, game_id: str, owner_key: str) -> tuple[AnalysisCheckpoint, ...]:
        statement = (
            select(AnalysisCheckpointRow)
            .where(
                AnalysisCheckpointRow.game_id == game_id,
                AnalysisCheckpointRow.owner_key == owner_key,
            )
            .order_by(AnalysisCheckpointRow.ply)
        )
        return tuple(_to_checkpoint(row) for row in self._session.scalars(statement))

    def _insert(self, checkpoint: AnalysisCheckpoint) -> AnalysisCheckpointRow:
        row = AnalysisCheckpointRow(
            game_id=checkpoint.game_id,
            ply=checkpoint.ply,
            owner_key=checkpoint.owner_key,
        )
        self._apply(row, checkpoint)
        try:
            # A savepoint keeps a lost insert race from discarding the caller's transaction.
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            # A concurrent analysis of the same move won the primary key; the
            # re-read must lock, because a plain SELECT would reuse a snapshot
            # predating that commit.
            concurrent = self._find_row(checkpoint.game_id, checkpoint.owner_key, checkpoint.ply, for_update=True)
            if concurrent is None:
                raise
            self._apply(concurrent, checkpoint)
            return concurrent
        return row

    def _find_row(
        self,
        game_id: str,
        owner_key: str,
        ply: int,
        for_update: bool = False,
    ) -> AnalysisCheckpointRow | None:
        statement = select(AnalysisCheckpointRow).where(
            AnalysisCheckpointRow.game_id == game_id,
            AnalysisCheckpointRow.owner_key == owner_key,
            AnalysisCheckpointRow.ply == ply,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalars(statement).one_or_none()

    def _require_own_game(self, game_id: str, owner_key: str) -> None:
        statement = select(GameRow.id).where(GameRow.id == game_id, GameRow.owner_key == owner_key)
        if self._session.scalars(statement).one_or_none() is None:
            raise UnknownCheckpointGameError(f"game {game_id} is not available for this owner")

    @staticmethod
    def _apply(row: AnalysisCheckpointRow, checkpoint: AnalysisCheckpoint) -> None:
        row.position_hash = checkpoint.position_hash
        row.score_before_centipawns = checkpoint.score_before.centipawns
        row.score_before_mate_in = checkpoint.score_before.mate_in
        row.score_after_centipawns = checkpoint.score_after.centipawns
        row.score_after_mate_in = checkpoint.score_after.mate_in
        row.centipawn_loss = checkpoint.centipawn_loss
        row.engine_depth = checkpoint.engine.depth
        row.engine_search_time_ms = checkpoint.engine.search_time_ms
        row.engine_skill_level = checkpoint.engine.skill_level
