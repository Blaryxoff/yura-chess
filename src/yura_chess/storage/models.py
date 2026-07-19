"""SQLAlchemy models. Column types are chosen for MariaDB 11.4 / InnoDB."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

OWNER_KEY_LENGTH = 64
FINGERPRINT_LENGTH = 64
TRANSCRIPT_TEXT_LENGTH = 255
POSITION_HASH_LENGTH = 64
IMAGE_ID_LENGTH = 128


class Base(DeclarativeBase):
    pass


class GameRow(Base):
    __tablename__ = "games"
    __table_args__ = (Index("ix_games_owner_status_last_player_move", "owner_key", "status", "last_player_move_at"),)

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    owner_key: Mapped[str] = mapped_column(CHAR(OWNER_KEY_LENGTH), index=True)
    status: Mapped[str] = mapped_column(String(16))
    player_color: Mapped[str] = mapped_column(String(5))
    initial_fen: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    engine_skill_level: Mapped[int] = mapped_column(SmallInteger)
    engine_move_time_ms: Mapped[int] = mapped_column(Integer)
    last_player_move_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    moves: Mapped[list[GameMoveRow]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        order_by="GameMoveRow.ply",
        lazy="selectin",
    )
    pending_engine_turn: Mapped[PendingEngineTurnRow | None] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class GameMoveRow(Base):
    __tablename__ = "game_moves"

    game_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("games.id", ondelete="CASCADE"), primary_key=True)
    ply: Mapped[int] = mapped_column(Integer, primary_key=True)
    uci: Mapped[str] = mapped_column(String(5))
    actor: Mapped[str] = mapped_column(String(6))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    game: Mapped[GameRow] = relationship(back_populates="moves")


class PendingEngineTurnRow(Base):
    """At most one unfinished engine reply per game."""

    __tablename__ = "pending_engine_turns"

    game_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("games.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(CHAR(36))
    player_move_uci: Mapped[str] = mapped_column(String(5))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    game: Mapped[GameRow] = relationship(back_populates="pending_engine_turn")


class AsrTranscriptRow(Base):
    """Corpus row for improving speech recognition.

    Deliberately narrow: the normalised utterance, what the resolver made of it
    and the pseudonymous owner. No audio, no token, no raw webhook payload and no
    direct Alice identifier is representable here.
    """

    __tablename__ = "asr_transcripts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_key: Mapped[str] = mapped_column(CHAR(OWNER_KEY_LENGTH), index=True)
    normalized_text: Mapped[str] = mapped_column(String(TRANSCRIPT_TEXT_LENGTH))
    outcome: Mapped[str] = mapped_column(String(16))
    # Percent rather than a float: the corpus is aggregated, not recomputed.
    confidence_percent: Mapped[int] = mapped_column(SmallInteger)
    candidate_count: Mapped[int] = mapped_column(SmallInteger)
    legal_move_count: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class BoardImageCacheRow(Base):
    """Which Yandex resource already holds the picture of a rendered position.

    Only the mapping and its timestamps live here — never the PNG itself, which
    exists solely in memory for the duration of one request.
    """

    __tablename__ = "board_image_cache"

    position_hash: Mapped[str] = mapped_column(CHAR(POSITION_HASH_LENGTH), primary_key=True)
    image_id: Mapped[str] = mapped_column(String(IMAGE_ID_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    # Bumped on every cache hit so eviction can drop the least recently used.
    last_used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class RequestReplayRow(Base):
    """Idempotency record keyed by the Alice request triple."""

    __tablename__ = "request_replays"
    __table_args__ = (UniqueConstraint("skill_id", "session_id", "message_id", name="uq_request_replays_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(CHAR(FINGERPRINT_LENGTH))
    owner_key: Mapped[str] = mapped_column(CHAR(OWNER_KEY_LENGTH))
    game_id: Mapped[str | None] = mapped_column(CHAR(36), ForeignKey("games.id", ondelete="CASCADE"), nullable=True)
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
