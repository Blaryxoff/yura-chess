"""Fixtures for repository integration tests.

These tests require a real MariaDB 11.4: the schema is created by running the
Alembic migrations, so the migration itself is exercised on every run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.storage.game_repository import GameRepository

TEST_DSN_ENV = "YURA_CHESS_TEST_DATABASE_URL"
_TABLES = ("request_replays", "pending_engine_turns", "game_moves", "games")


def _dsn() -> str:
    dsn = os.environ.get(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} is not set; repository tests need a real MariaDB")
    return dsn


@pytest.fixture(scope="session")
def database_engine() -> Iterator[Engine]:
    dsn = _dsn()
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


@pytest.fixture(autouse=True)
def clean_tables(database_engine: Engine) -> Iterator[None]:
    yield
    with database_engine.begin() as connection:
        for table in _TABLES:
            connection.execute(text(f"DELETE FROM {table}"))


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def repository(session: Session) -> GameRepository:
    return GameRepository(session)
