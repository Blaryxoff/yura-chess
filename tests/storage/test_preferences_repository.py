"""Durable preferences: their defaults, their limits and their owner boundary."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.domain.game import PlayerColor
from yura_chess.domain.preferences import (
    DEFAULT_BOARD_ORIENTATION,
    DEFAULT_DETAIL_LEVEL,
    DEFAULT_GAME_MODE,
    DEFAULT_NOTATION_STYLE,
    DEFAULT_PAUSE_STYLE,
    BoardOrientation,
    DetailLevel,
    GameMode,
    NotationStyle,
    PauseStyle,
    PlayerPreferences,
)
from yura_chess.storage.database import run_transaction_with_deadlock_retry
from yura_chess.storage.models import PlayerPreferencesRow
from yura_chess.storage.preferences_repository import PreferencesRepository

OWNER = "a" * 64
OTHER_OWNER = "b" * 64

# Anything that would tie a preference row to the Alice request that set it.
FORBIDDEN_COLUMNS = frozenset({"user_id", "application_id", "session_id", "message_id", "token", "access_token"})

DOMAIN_ENUMS = {
    "detail_level": DetailLevel,
    "pause_style": PauseStyle,
    "notation_style": NotationStyle,
    "board_orientation": BoardOrientation,
    "default_mode": GameMode,
}


class _RecordingSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _operational_error(code: int) -> OperationalError:
    return OperationalError("INSERT", {}, Exception(code, "database failure"))


@pytest.fixture
def preferences(session: Session) -> PreferencesRepository:
    return PreferencesRepository(session)


def test_schema_holds_only_owner_scoped_preferences() -> None:
    columns = {column.name for column in inspect(PlayerPreferencesRow).columns}

    assert columns & FORBIDDEN_COLUMNS == set()
    assert columns == {
        "owner_key",
        "detail_level",
        "pause_style",
        "notation_style",
        "board_orientation",
        "default_mode",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize(("column_name", "domain_enum"), DOMAIN_ENUMS.items())
def test_column_values_and_defaults_match_the_domain(column_name: str, domain_enum: type) -> None:
    column = inspect(PlayerPreferencesRow).columns[column_name]
    default_field = getattr(PlayerPreferences(owner_key=OWNER), column_name)

    assert set(column.type.enums) == {member.value for member in domain_enum}
    assert column.server_default.arg == default_field.value


def test_documented_defaults_are_the_dataclass_defaults() -> None:
    assert DEFAULT_DETAIL_LEVEL is DetailLevel.NORMAL
    assert DEFAULT_PAUSE_STYLE is PauseStyle.NORMAL
    assert DEFAULT_NOTATION_STYLE is NotationStyle.FULL
    assert DEFAULT_BOARD_ORIENTATION is BoardOrientation.PLAYER
    assert DEFAULT_GAME_MODE is GameMode.GAME


def test_a_mariadb_deadlock_retries_once_in_a_fresh_transaction() -> None:
    sessions: list[_RecordingSession] = []
    calls = 0

    def factory() -> _RecordingSession:
        session = _RecordingSession()
        sessions.append(session)
        return session

    def operation(session: _RecordingSession) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _operational_error(1213)
        assert session is sessions[1]
        return "saved"

    assert run_transaction_with_deadlock_retry(factory, operation) == "saved"  # type: ignore[arg-type]
    assert len(sessions) == 2
    assert (sessions[0].rolled_back, sessions[0].closed) == (True, True)
    assert (sessions[1].committed, sessions[1].closed) == (True, True)


def test_a_non_deadlock_operational_error_is_not_retried() -> None:
    sessions: list[_RecordingSession] = []

    def factory() -> _RecordingSession:
        session = _RecordingSession()
        sessions.append(session)
        return session

    def operation(_session: _RecordingSession) -> None:
        raise _operational_error(1205)

    with pytest.raises(OperationalError):
        run_transaction_with_deadlock_retry(
            factory,  # type: ignore[arg-type]
            operation,  # type: ignore[arg-type]
        )

    assert len(sessions) == 1


def test_unset_owner_reads_defaults_without_writing_a_row(session: Session, preferences: PreferencesRepository) -> None:
    stored = preferences.load(OWNER)

    assert stored == PlayerPreferences(owner_key=OWNER)
    assert session.scalars(select(PlayerPreferencesRow)).all() == []


def test_default_orientation_follows_the_player_and_starts_white() -> None:
    stored = PlayerPreferences(owner_key=OWNER)

    assert stored.orientation_for(PlayerColor.BLACK) is PlayerColor.BLACK
    assert stored.orientation_for(None) is PlayerColor.WHITE


def test_pinned_orientation_ignores_the_player_colour() -> None:
    stored = PlayerPreferences(owner_key=OWNER, board_orientation=BoardOrientation.BLACK)

    assert stored.orientation_for(PlayerColor.WHITE) is PlayerColor.BLACK
    assert stored.orientation_for(None) is PlayerColor.BLACK


def test_migrated_row_defaults_match_the_domain(session: Session, preferences: PreferencesRepository) -> None:
    session.execute(text("INSERT INTO player_preferences (owner_key) VALUES (:owner)"), {"owner": OWNER})
    session.flush()

    assert preferences.load(OWNER) == PlayerPreferences(owner_key=OWNER)


def test_saving_twice_updates_the_single_row(session: Session, preferences: PreferencesRepository) -> None:
    wanted = PlayerPreferences(
        owner_key=OWNER,
        detail_level=DetailLevel.BRIEF,
        pause_style=PauseStyle.EXTENDED,
        notation_style=NotationStyle.SHORT,
        board_orientation=BoardOrientation.BLACK,
        default_mode=GameMode.TRAINING,
    )

    first = preferences.save(wanted)
    second = preferences.save(wanted)

    assert first == wanted
    assert second == wanted
    assert len(session.scalars(select(PlayerPreferencesRow)).all()) == 1


def test_preferences_survive_a_new_session(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as writing:
        PreferencesRepository(writing).save(
            PlayerPreferences(owner_key=OWNER, detail_level=DetailLevel.DETAILED),
        )
        writing.commit()

    with session_factory() as reading:
        assert PreferencesRepository(reading).load(OWNER).detail_level is DetailLevel.DETAILED


def test_one_owner_cannot_read_or_change_another(session: Session, preferences: PreferencesRepository) -> None:
    preferences.save(PlayerPreferences(owner_key=OWNER, notation_style=NotationStyle.SHORT))

    assert preferences.load(OTHER_OWNER) == PlayerPreferences(owner_key=OTHER_OWNER)

    preferences.save(PlayerPreferences(owner_key=OTHER_OWNER, notation_style=NotationStyle.FULL))

    assert preferences.load(OWNER).notation_style is NotationStyle.SHORT
    assert len(session.scalars(select(PlayerPreferencesRow)).all()) == 2


def test_database_rejects_a_value_outside_the_enum(session: Session) -> None:
    with pytest.raises(DBAPIError):
        session.execute(
            text("INSERT INTO player_preferences (owner_key, detail_level) VALUES (:owner, 'shouty')"),
            {"owner": OWNER},
        )
        session.flush()
    session.rollback()
