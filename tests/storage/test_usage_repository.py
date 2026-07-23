"""Permanent aggregate analytics without direct Alice identifiers."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import UsageRequestRow, UsageUserRow
from yura_chess.storage.usage_repository import UsageRepository

REAL_OWNER = "a" * 64
TEST_OWNER = "b" * 64


def test_usage_schema_cannot_store_raw_identifiers_or_conversation_data() -> None:
    user_columns = {column.name for column in inspect(UsageUserRow).columns}
    request_columns = {column.name for column in inspect(UsageRequestRow).columns}

    assert user_columns == {"owner_key", "traffic_source", "first_seen_at", "last_seen_at"}
    assert request_columns == {"request_key", "owner_key", "session_key", "created_at"}


def test_recording_is_idempotent_and_test_classification_never_downgrades(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 7, 23, 12, 0, 0)

    repository.record_request(REAL_OWNER, "skill", "raw-session", "1", "real", now)
    repository.record_request(REAL_OWNER, "skill", "raw-session", "1", "real", now)
    repository.record_request(REAL_OWNER, "skill", "test-session", "2", "test", now + timedelta(minutes=1))
    repository.record_request(REAL_OWNER, "skill", "later-session", "3", "real", now + timedelta(minutes=2))
    session.commit()

    user = session.get(UsageUserRow, REAL_OWNER)
    requests = session.scalars(select(UsageRequestRow).order_by(UsageRequestRow.created_at)).all()
    assert user is not None
    assert user.traffic_source == "test"
    assert user.first_seen_at == now
    assert user.last_seen_at == now + timedelta(minutes=2)
    assert len(requests) == 3
    assert all(row.session_key not in {"raw-session", "test-session", "later-session"} for row in requests)


def test_dashboard_separates_real_test_and_all_traffic(session: Session) -> None:
    now = datetime(2026, 7, 23, 12, 0, 0)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "real-session", "1", "real", now)
    usage.record_request(REAL_OWNER, "skill", "real-session", "2", "real", now + timedelta(minutes=1))
    usage.record_request(TEST_OWNER, "skill", "test-session", "1", "test", now)
    games = GameRepository(session)
    real_game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(real_game.id, REAL_OWNER, real_game.revision, ("e2e4", "e7e5"), GameStatus.FINISHED)
    games.create_game(TEST_OWNER, PlayerColor.WHITE)
    session.commit()

    real = usage.dashboard("real", now + timedelta(hours=1), period="all").totals
    test = usage.dashboard("test", now + timedelta(hours=1), period="all").totals
    all_traffic = usage.dashboard("all", now + timedelta(hours=1), period="all").totals

    assert (real.requests, real.users, real.sessions) == (2, 1, 1)
    assert (real.games, real.engaged_games, real.player_moves, real.finished_games) == (1, 1, 1, 1)
    assert (test.requests, test.users, test.sessions, test.games) == (1, 1, 1, 1)
    assert (all_traffic.requests, all_traffic.users, all_traffic.sessions, all_traffic.games) == (3, 2, 2, 2)


def test_dashboard_chart_supports_month_year_and_all_time_periods(session: Session) -> None:
    now = datetime(2026, 7, 23, 12, 0, 0)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "old-session", "1", "real", datetime(2025, 5, 2, 12, 0, 0))
    usage.record_request(REAL_OWNER, "skill", "current-session", "1", "real", now)
    session.commit()

    month_snapshot = usage.dashboard("real", now, period="month")
    year_snapshot = usage.dashboard("real", now, period="year")
    all_time_snapshot = usage.dashboard("real", now, period="all")
    month = month_snapshot.daily
    year = year_snapshot.daily
    all_time = all_time_snapshot.daily

    assert len(month) == 30
    assert (month[0].day, month[-1].day, sum(point.requests for point in month)) == (
        date(2026, 6, 24),
        date(2026, 7, 23),
        1,
    )
    assert len(year) == 12
    assert (year[0].day, year[-1].day, sum(point.requests for point in year)) == (
        date(2025, 8, 1),
        date(2026, 7, 1),
        1,
    )
    assert (all_time[0].day, all_time[-1].day, sum(point.requests for point in all_time)) == (
        date(2025, 5, 1),
        date(2026, 7, 1),
        2,
    )
    assert month_snapshot.totals.requests == year_snapshot.totals.requests == 1
    assert all_time_snapshot.totals.requests == 2
