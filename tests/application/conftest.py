"""Service tests use a real MariaDB and fake engine workers — never a real Stockfish."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from database_fixtures import truncate_tables
from sqlalchemy import Engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_tables(database_engine: Engine) -> Iterator[None]:
    yield
    truncate_tables(database_engine)
