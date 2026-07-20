"""The end-to-end suite runs the real services against a real MariaDB and a fake engine."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from database_fixtures import truncate_tables


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_tables(request: pytest.FixtureRequest) -> Iterator[None]:
    """Empty the local tables after every test that uses them.

    The deployed-webhook module talks to production over HTTP and owns no local
    database, so it opts out instead of being skipped for a missing test DSN.
    """
    if request.node.get_closest_marker("deployed") is not None:
        yield
        return
    engine = request.getfixturevalue("database_engine")
    yield
    truncate_tables(engine)
