"""Settings values shared by the layers that build an application or a client.

Kept out of `conftest.py` so test modules in any directory can import them by
module name instead of shadowing their own package's conftest.
"""

from __future__ import annotations

import pytest

from yura_chess.settings import Settings

TEST_IDENTITY_SALT = "test-identity-salt"

# Points at a closed port on purpose: tests using it must not reach a database.
UNREACHABLE_DATABASE_URL = "mysql+pymysql://user:pass@127.0.0.1:13306/yura_chess_unreachable?charset=utf8mb4"


@pytest.fixture
def offline_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        environment="test",
        database_url=UNREACHABLE_DATABASE_URL,
        identity_salt=TEST_IDENTITY_SALT,
    )
