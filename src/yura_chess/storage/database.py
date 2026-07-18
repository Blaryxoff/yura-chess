"""Engine and session management for the MariaDB backend."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.settings import Settings
from yura_chess.storage.models import Base

REQUIRED_TABLES = frozenset(Base.metadata.tables)


class SchemaMismatchError(RuntimeError):
    """The connected database does not carry the tables this build expects."""


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        str(settings.database_url),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        pool_recycle=settings.database_pool_recycle_seconds,
        future=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """One short transaction: commit on success, roll back on any error."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def check_schema(engine: Engine) -> None:
    present = set(inspect(engine).get_table_names())
    missing = sorted(REQUIRED_TABLES - present)
    if missing:
        raise SchemaMismatchError(f"missing tables: {', '.join(missing)}")
