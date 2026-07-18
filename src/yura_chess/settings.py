from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, MySQLDsn, SecretStr
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
    # No default: the salt is what keeps stored owner keys unlinkable to Alice accounts.
    identity_salt: SecretStr
    # Alice drops the answer after five seconds; the skill answers within 4.5.
    webhook_deadline_seconds: float = Field(default=4.5, gt=0.0, le=5.0)
    database_pool_size: int = 5
    database_max_overflow: int = 5
    database_pool_recycle_seconds: int = 900
    stockfish_path: Path = Path("/usr/games/stockfish")
    engine_pool_size: int = Field(default=2, ge=1, le=8)
    engine_threads: int = Field(default=1, ge=1)
    engine_hash_mb: int = Field(default=64, ge=1)
    engine_skill_level: int = Field(default=5, ge=0, le=20)
    engine_acquire_timeout_seconds: float = Field(default=0.5, gt=0.0, le=1.0)
    # The Alice webhook budget is 4.5 s; a search may never eat more than three of them.
    engine_move_deadline_seconds: float = Field(default=3.0, gt=0.0, le=3.0)
    engine_move_time_seconds: float = Field(default=1.0, gt=0.0)
    engine_restart_delay_seconds: float = Field(default=1.0, gt=0.0)
    # Below this the skill asks instead of moving: a misheard move is worse than a question.
    voice_move_confidence_threshold: float = Field(default=0.7, gt=0.0, le=1.0)
    # The screen card is optional everywhere: without credentials the skill stays voice-only.
    board_image_enabled: bool = True
    yandex_skill_id: str | None = None
    yandex_oauth_token: SecretStr | None = None
    # Whatever is left of the 4.5 s budget after speech; an upload never gets more.
    image_upload_timeout_seconds: float = Field(default=1.0, gt=0.0, le=2.0)
    board_image_ttl_days: int = Field(default=30, ge=1)
    board_image_cache_limit: int = Field(default=5000, ge=1)
    asr_transcript_retention_days: int = Field(default=30, ge=1)
    asr_transcript_text_limit: int = Field(default=255, ge=16, le=255)


@lru_cache
def get_settings() -> Settings:
    # Required fields are supplied by the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]
