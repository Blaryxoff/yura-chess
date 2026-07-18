from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    database_path: Path = Path("var/yura_chess.sqlite3")
    stockfish_path: Path = Path("/usr/games/stockfish")


@lru_cache
def get_settings() -> Settings:
    return Settings()
