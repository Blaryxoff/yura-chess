import pytest

from yura_chess.settings import Settings

# Points at a closed port on purpose: tests using it must not reach a database.
UNREACHABLE_DATABASE_URL = "mysql+pymysql://user:pass@127.0.0.1:13306/yura_chess_unreachable?charset=utf8mb4"


@pytest.fixture
def offline_settings() -> Settings:
    return Settings(environment="test", database_url=UNREACHABLE_DATABASE_URL)  # type: ignore[arg-type]
