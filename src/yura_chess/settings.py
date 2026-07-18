from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import MySQLDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="YURA_CHESS_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    # No default: the DSN carries credentials and must come from the environment.
    database_url: MySQLDsn
    database_pool_size: int = 5
    database_max_overflow: int = 5
    database_pool_recycle_seconds: int = 900
    stockfish_path: Path = Path("/usr/games/stockfish")


@lru_cache
def get_settings() -> Settings:
    # Required fields are supplied by the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]
