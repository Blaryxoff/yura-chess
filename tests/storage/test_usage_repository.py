"""Permanent aggregate analytics without direct Alice identifiers."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from yura_chess.application.command_router import CommandKind
from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import (
    GameMoveRow,
    GameRow,
    PuzzleAttemptRow,
    PuzzleProfileRow,
    UsageRequestRow,
    UsageUserRow,
)
from yura_chess.storage.usage_repository import DailyUsage, UsageRepository

REAL_OWNER = "a" * 64
TEST_OWNER = "b" * 64


def test_usage_schema_cannot_store_raw_identifiers_or_conversation_data() -> None:
    user_columns = {column.name for column in inspect(UsageUserRow).columns}
    request_columns = {column.name for column in inspect(UsageRequestRow).columns}

    assert user_columns == {
        "owner_key",
        "traffic_source",
        "first_seen_at",
        "last_seen_at",
        "review_prompted_at",
    }
    assert request_columns == {
        "request_key",
        "owner_key",
        "session_key",
        "release_id",
        "command_kind",
        "resolution_status",
        "routing_outcome",
        "created_at",
    }


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


def test_request_quality_fields_upgrade_an_existing_idempotent_event(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 7, 24, 12, 0, 0)
    repository.record_request(REAL_OWNER, "skill", "session", "1", "real", now)
    repository.record_request(
        REAL_OWNER,
        "skill",
        "session",
        "1",
        "real",
        now + timedelta(seconds=1),
        release_id="ghcr.io/example/yura-chess:abc123",
        command_kind="move",
        resolution_status="resolved",
        routing_outcome="handled",
    )
    session.commit()

    row = session.scalars(select(UsageRequestRow)).one()
    assert row.created_at == now
    assert row.release_id == "ghcr.io/example/yura-chess:abc123"
    assert (row.command_kind, row.resolution_status, row.routing_outcome) == ("move", "resolved", "handled")


def test_review_prompt_is_claimed_once_after_an_engaged_game_finishes(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 8, 30, 12, 0, 0)
    repository.record_request(REAL_OWNER, "skill", "session", "1", "real", now)
    game = GameRepository(session).create_game(REAL_OWNER, PlayerColor.WHITE)
    GameRepository(session).append_moves(
        game.id,
        REAL_OWNER,
        game.revision,
        ("e2e4", "e7e5"),
        GameStatus.FINISHED,
    )

    assert repository.claim_review_prompt(REAL_OWNER, now) is True
    assert repository.claim_review_prompt(REAL_OWNER, now + timedelta(minutes=1)) is False
    assert session.get(UsageUserRow, REAL_OWNER).review_prompted_at == now


def test_review_prompt_requires_real_traffic_and_a_value_milestone(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 8, 30, 12, 0, 0)
    repository.record_request(REAL_OWNER, "skill", "session", "1", "real", now)
    for index in range(3):
        repository.record_request(TEST_OWNER, "skill", f"session-{index}", "1", "test", now)
    game = GameRepository(session).create_game(TEST_OWNER, PlayerColor.WHITE)
    GameRepository(session).append_moves(game.id, TEST_OWNER, game.revision, ("e2e4", "e7e5"))

    assert repository.claim_review_prompt(REAL_OWNER, now) is False
    assert repository.claim_review_prompt(TEST_OWNER, now) is False


def test_three_clean_puzzles_unlock_the_review_prompt(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 8, 30, 12, 0, 0)
    repository.record_request(REAL_OWNER, "skill", "session", "1", "real", now)
    session.add(PuzzleProfileRow(owner_key=REAL_OWNER, clean_streak=3))
    session.flush()

    assert repository.claim_review_prompt(REAL_OWNER, now) is True


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


def test_chart_series_carry_every_selectable_metric(session: Session) -> None:
    played = datetime(2026, 7, 22, 12, 0, 0)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "session", "1", "real", played)
    usage.record_request(REAL_OWNER, "skill", "session", "2", "real", played + timedelta(minutes=1))
    games = GameRepository(session)
    game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(game.id, REAL_OWNER, game.revision, ("e2e4", "e7e5"))
    session.add(PuzzleAttemptRow(owner_key=REAL_OWNER, puzzle_id="abc123", created_at=played, updated_at=played))
    session.flush()
    rows = {row.id: row for row in session.scalars(select(GameRow))}
    rows[game.id].created_at = played
    for move in session.scalars(select(GameMoveRow)):
        move.created_at = played
    session.commit()

    month = {point.day: point for point in usage.dashboard("real", played, period="month").daily}
    all_time = {point.day: point for point in usage.dashboard("real", played, period="all").daily}
    day = month[date(2026, 7, 22)]
    bucket = all_time[date(2026, 7, 1)]

    assert (day.requests, day.users, day.sessions, day.new_users) == (2, 1, 1, 1)
    assert (day.games, day.player_moves, day.engaged_games, day.puzzle_attempts) == (1, 1, 1, 1)
    assert (bucket.requests, bucket.users, bucket.sessions) == (2, 1, 1)
    assert (bucket.games, bucket.player_moves, bucket.engaged_games, bucket.puzzle_attempts) == (1, 1, 1, 1)
    assert month[date(2026, 7, 21)] == DailyUsage(date(2026, 7, 21))


def test_new_and_returning_users_split_the_active_ones(session: Session) -> None:
    newcomer = "c" * 64
    first = datetime(2026, 7, 21, 12, 0, 0)
    second = datetime(2026, 7, 22, 12, 0, 0)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "first-visit", "1", "real", first)
    usage.record_request(REAL_OWNER, "skill", "second-visit", "2", "real", second)
    usage.record_request(newcomer, "skill", "only-visit", "1", "real", second)
    session.commit()

    snapshot = usage.dashboard("real", second, period="month")
    days = {point.day: point for point in snapshot.daily}
    all_time = usage.dashboard("real", second, period="all")
    july = all_time.daily[-1]

    assert (days[date(2026, 7, 21)].users, days[date(2026, 7, 21)].new_users) == (1, 1)
    assert days[date(2026, 7, 21)].returning_users == 0
    assert (days[date(2026, 7, 22)].users, days[date(2026, 7, 22)].new_users) == (2, 1)
    assert days[date(2026, 7, 22)].returning_users == 1
    # A month bucket asks the same question of the month: both owners arrive in July,
    # so a day-chart return does not count as one here.
    assert (july.day, july.users, july.new_users, july.returning_users) == (date(2026, 7, 1), 2, 2, 0)
    assert sum(point.new_users for point in all_time.daily) == all_time.totals.users


def test_dashboard_groups_utc_timestamps_by_moscow_day_and_month(session: Session) -> None:
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "june", "1", "real", datetime(2026, 6, 30, 20, 59, 59))
    usage.record_request(REAL_OWNER, "skill", "july", "1", "real", datetime(2026, 6, 30, 21, 0, 0))
    usage.record_request(REAL_OWNER, "skill", "before-midnight", "1", "real", datetime(2026, 7, 23, 20, 59, 59))
    usage.record_request(REAL_OWNER, "skill", "after-midnight", "1", "real", datetime(2026, 7, 23, 21, 0, 0))
    session.commit()

    month = usage.dashboard("real", datetime(2026, 7, 23, 21, 30, 0), period="month").daily
    all_time = usage.dashboard("real", datetime(2026, 7, 23, 21, 30, 0), period="all").daily

    daily_requests = {point.day: point.requests for point in month}
    monthly_requests = {point.day: point.requests for point in all_time}
    assert month[-1].day == date(2026, 7, 24)
    assert (daily_requests[date(2026, 7, 23)], daily_requests[date(2026, 7, 24)]) == (1, 1)
    assert (monthly_requests[date(2026, 6, 1)], monthly_requests[date(2026, 7, 1)]) == (1, 3)


def test_every_recordable_command_kind_fits_the_column() -> None:
    """A longer command value would fail the insert, not shorten the usage row."""
    limit = UsageRequestRow.__table__.columns["command_kind"].type.length
    recordable = [kind.value for kind in CommandKind]

    assert [value for value in recordable if len(value) > limit] == []
