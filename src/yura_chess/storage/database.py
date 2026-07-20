"""Engine and session management for the MariaDB backend."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.settings import Settings
from yura_chess.storage.models import Base

REQUIRED_TABLES = frozenset(Base.metadata.tables)
_MARIADB_DEADLOCK_CODE = 1213


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


def run_transaction_with_deadlock_retry[T](
    session_factory: sessionmaker[Session],
    operation: Callable[[Session], T],
) -> T:
    """Run one short transaction, retrying one MariaDB deadlock in a fresh session."""
    for attempt in range(2):
        try:
            with session_scope(session_factory) as session:
                result = operation(session)
            return result
        except OperationalError as error:
            if attempt == 1 or not _is_mariadb_deadlock(error):
                raise
    raise AssertionError("deadlock retry loop did not return or raise")


def _is_mariadb_deadlock(error: OperationalError) -> bool:
    arguments = getattr(error.orig, "args", ())
    return arguments[:1] == (_MARIADB_DEADLOCK_CODE,)


def check_connection(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def check_schema(engine: Engine) -> None:
    present = set(inspect(engine).get_table_names())
    missing = sorted(REQUIRED_TABLES - present)
    if missing:
        raise SchemaMismatchError(f"missing tables: {', '.join(missing)}")
