"""Repository integration tests run against a real MariaDB 11.4.

The schema is created by running the Alembic migrations, so the migration itself
is exercised on every run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from database_fixtures import truncate_tables
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from yura_chess.storage.game_repository import GameRepository


@pytest.fixture(autouse=True)
def clean_tables(database_engine: Engine) -> Iterator[None]:
    yield
    truncate_tables(database_engine)


@pytest.fixture
def repository(session: Session) -> GameRepository:
    return GameRepository(session)
