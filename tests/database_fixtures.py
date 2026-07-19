"""Shared MariaDB fixtures for the layers that must be tested against a real database.

Registered as a plugin from `tests/conftest.py`, but deliberately without an
autouse cleaner: only the directories that touch the database declare one, so
health and engine tests stay database-free.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DSN_ENV = "YURA_CHESS_TEST_DATABASE_URL"
_TABLES = (
    "board_image_cache",
    "asr_transcripts",
    "player_preferences",
    "request_replays",
    "analysis_checkpoints",
    "game_reviews",
    "puzzle_attempts",
    "puzzle_profiles",
    "pending_engine_turns",
    "game_moves",
    "games",
)


def truncate_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in _TABLES:
            connection.execute(text(f"DELETE FROM {table}"))


@pytest.fixture(scope="session")
def database_engine() -> Iterator[Engine]:
    dsn = os.environ.get(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} is not set; these tests need a real MariaDB")
    engine = create_engine(dsn, future=True)
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    os.environ["ALEMBIC_DATABASE_URL"] = dsn
    command.upgrade(config, "head")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=database_engine, expire_on_commit=False, future=True)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session
