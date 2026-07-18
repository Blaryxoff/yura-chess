"""Speech tests are pure; the image cache test needs a backend and a clean table."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from database_fixtures import truncate_tables
from sqlalchemy import Engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clean_image_cache(database_engine: Engine) -> Iterator[None]:
    yield
    truncate_tables(database_engine)
