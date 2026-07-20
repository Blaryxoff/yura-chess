"""Owner-scoped storage for durable presentation preferences.

Reading is total: an owner who never changed anything gets the documented
defaults without a row being written. Writing is an upsert on the owner key, so
repeating the same request leaves exactly the same single row.

No Alice identifier is representable here — the pseudonymous owner key is the
only subject of a row.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yura_chess.domain.preferences import (
    BoardOrientation,
    DetailLevel,
    GameMode,
    NotationStyle,
    PauseStyle,
    PlayerPreferences,
)
from yura_chess.storage.models import PlayerPreferencesRow


def _to_preferences(row: PlayerPreferencesRow) -> PlayerPreferences:
    return PlayerPreferences(
        owner_key=row.owner_key,
        detail_level=DetailLevel(row.detail_level),
        pause_style=PauseStyle(row.pause_style),
        notation_style=NotationStyle(row.notation_style),
        board_orientation=BoardOrientation(row.board_orientation),
        default_mode=GameMode(row.default_mode),
    )


class PreferencesRepository:
    """Thin data-access layer bound to one short transaction (one Session)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self, owner_key: str, for_update: bool = False) -> PlayerPreferences:
        row = self._find_row(owner_key, for_update=for_update)
        if row is None and for_update:
            # MariaDB serialises this per primary key without the deadlock-prone
            # insert/savepoint race that two first-time Alice sessions can hit.
            statement = mysql_insert(PlayerPreferencesRow).values(owner_key=owner_key)
            self._session.execute(statement.on_duplicate_key_update(owner_key=statement.inserted.owner_key))
            row = self._find_row(owner_key, for_update=True)
        if row is None:
            return PlayerPreferences(owner_key=owner_key)
        return _to_preferences(row)

    def save(self, preferences: PlayerPreferences) -> PlayerPreferences:
        """Store the whole preference set for its owner, creating the row if needed."""
        row = self._find_row(preferences.owner_key)
        if row is None:
            row = self._insert(preferences)
        else:
            self._apply(row, preferences)
        self._session.flush()
        return _to_preferences(row)

    def _insert(self, preferences: PlayerPreferences) -> PlayerPreferencesRow:
        row = PlayerPreferencesRow(owner_key=preferences.owner_key)
        self._apply(row, preferences)
        try:
            # A savepoint keeps a lost insert race from discarding the caller's transaction.
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            # A concurrent request created the row first; the re-read must lock,
            # because a plain SELECT would reuse a snapshot predating that commit.
            concurrent = self._find_row(preferences.owner_key, for_update=True)
            if concurrent is None:
                raise
            self._apply(concurrent, preferences)
            return concurrent
        return row

    def _find_row(self, owner_key: str, for_update: bool = False) -> PlayerPreferencesRow | None:
        statement = select(PlayerPreferencesRow).where(PlayerPreferencesRow.owner_key == owner_key)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalars(statement).one_or_none()

    @staticmethod
    def _apply(row: PlayerPreferencesRow, preferences: PlayerPreferences) -> None:
        row.detail_level = preferences.detail_level.value
        row.pause_style = preferences.pause_style.value
        row.notation_style = preferences.notation_style.value
        row.board_orientation = preferences.board_orientation.value
        row.default_mode = preferences.default_mode.value
