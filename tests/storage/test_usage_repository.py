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
from yura_chess.storage.puzzle_repository import PuzzleRepository
from yura_chess.storage.usage_repository import DailyUsage, UsageRepository

REAL_OWNER = "a" * 64
TEST_OWNER = "b" * 64


def record_action(repository: UsageRepository, owner: str, session_id: str, message_id: str, when: datetime) -> None:
    repository.record_request(
        owner,
        "skill",
        session_id,
        message_id,
        "real",
        when,
        command_kind="help",
        routing_outcome="handled",
    )


def test_usage_schema_cannot_store_raw_identifiers_or_conversation_data() -> None:
    user_columns = {column.name for column in inspect(UsageUserRow).columns}
    request_columns = {column.name for column in inspect(UsageRequestRow).columns}

    assert user_columns == {
        "owner_key",
        "traffic_source",
        "first_seen_at",
        "last_seen_at",
        "review_prompted_at",
        "review_prompt_count",
        "review_prompts_disabled",
    }
    assert request_columns == {
        "request_key",
        "owner_key",
        "session_key",
        "release_id",
        "command_kind",
        "resolution_status",
        "routing_outcome",
        "review_prompt_kind",
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


def test_review_prompts_have_one_reminder_after_thirty_days_and_permanent_opt_out(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 8, 30, 12)
    repository.record_request(REAL_OWNER, "skill", "session", "1", "real", now)
    session.add(PuzzleProfileRow(owner_key=REAL_OWNER, clean_streak=3))
    session.flush()

    assert repository.claim_review_prompt(REAL_OWNER, now)
    assert not repository.claim_review_prompt(REAL_OWNER, now + timedelta(days=30, microseconds=-1))
    assert repository.claim_review_prompt(REAL_OWNER, now + timedelta(days=30))
    assert not repository.claim_review_prompt(REAL_OWNER, now + timedelta(days=60))
    repository.disable_review_prompts(REAL_OWNER)
    user = session.get(UsageUserRow, REAL_OWNER)
    assert user.review_prompt_count == 2
    assert user.review_prompts_disabled


def test_opt_out_blocks_eligible_prompts_without_affecting_another_user(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 8, 30, 12)
    for owner in (REAL_OWNER, TEST_OWNER):
        repository.record_request(owner, "skill", owner, "1", "real", now)
        session.add(PuzzleProfileRow(owner_key=owner, clean_streak=3))
    session.flush()
    repository.disable_review_prompts(REAL_OWNER)

    assert not repository.claim_review_prompt(REAL_OWNER, now)
    assert repository.claim_review_prompt(TEST_OWNER, now)


def test_replaced_games_and_repeat_visits_do_not_unlock_review_prompts(session: Session) -> None:
    repository = UsageRepository(session)
    now = datetime(2026, 8, 30, 12)
    for index in range(3):
        repository.record_request(REAL_OWNER, "skill", f"visit-{index}", "1", "real", now)
    games = GameRepository(session)
    game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(game.id, REAL_OWNER, game.revision, ("e2e4", "e7e5"), GameStatus.RESIGNED)

    assert not repository.claim_review_prompt(REAL_OWNER, now)


def test_dashboard_separates_real_test_and_all_traffic(session: Session) -> None:
    now = datetime(2026, 7, 23, 12, 0, 0)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "real-session", "1", "real", now)
    record_action(usage, REAL_OWNER, "real-session", "2", now + timedelta(minutes=1))
    usage.record_request(TEST_OWNER, "skill", "test-session", "1", "test", now)
    games = GameRepository(session)
    real_game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(real_game.id, REAL_OWNER, real_game.revision, ("e2e4", "e7e5"), GameStatus.FINISHED)
    games.create_game(TEST_OWNER, PlayerColor.WHITE)
    session.commit()

    real = usage.dashboard("real", now + timedelta(hours=1), period="all").totals
    test = usage.dashboard("test", now + timedelta(hours=1), period="all").totals
    all_traffic = usage.dashboard("all", now + timedelta(hours=1), period="all").totals

    assert (real.launches, real.actions, real.users, real.sessions) == (1, 1, 1, 1)
    assert (real.engaged_games, real.player_moves, real.finished_games) == (1, 1, 1)
    assert (test.launches, test.actions, test.users, test.sessions) == (1, 0, 0, 0)
    assert (all_traffic.launches, all_traffic.actions, all_traffic.users, all_traffic.sessions) == (2, 1, 1, 1)


def test_launches_do_not_make_an_accidental_visitor_active(session: Session) -> None:
    now = datetime(2026, 7, 23, 12)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "accidental", "0", "real", now)
    usage.record_request(
        REAL_OWNER,
        "skill",
        "accidental",
        "1",
        "real",
        now + timedelta(minutes=1),
        command_kind="exit",
        routing_outcome="handled",
    )
    usage.record_request(
        TEST_OWNER,
        "skill",
        "intentional",
        "0",
        "real",
        now,
        command_kind="move",
        resolution_status="ambiguous",
        routing_outcome="handled",
    )
    usage.record_request(
        TEST_OWNER,
        "skill",
        "intentional",
        "1",
        "real",
        now + timedelta(minutes=2),
        command_kind="illegal_move",
        routing_outcome="illegal_move",
    )
    session.commit()

    snapshot = usage.dashboard("real", now + timedelta(hours=1))

    assert (snapshot.totals.launches, snapshot.totals.actions, snapshot.totals.users, snapshot.totals.sessions) == (
        2,
        1,
        1,
        1,
    )
    assert snapshot.daily[-1].new_users == 1


def test_launch_is_grouped_on_the_first_request_even_when_action_is_later(session: Session) -> None:
    usage = UsageRepository(session)
    first = datetime(2026, 6, 1, 12)
    usage.record_request(REAL_OWNER, "skill", "one-session", "0", "real", first)
    record_action(usage, REAL_OWNER, "one-session", "1", datetime(2026, 7, 1, 12))
    session.commit()

    july = usage.dashboard("real", datetime(2026, 7, 2, 12), period="month")

    assert (july.totals.launches, july.totals.actions, july.totals.users, july.totals.sessions) == (0, 1, 1, 1)


def test_replaced_games_are_not_completed_games(session: Session) -> None:
    now = datetime(2026, 7, 23, 12)
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "session", "1", now)
    games = GameRepository(session)
    replaced = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(replaced.id, REAL_OWNER, replaced.revision, ("e2e4", "e7e5"), GameStatus.RESIGNED)
    completed = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(completed.id, REAL_OWNER, completed.revision, ("e2e4", "e7e5"), GameStatus.FINISHED)
    session.commit()

    assert usage.dashboard("real", now + timedelta(hours=1), period="all").totals.finished_games == 1


def test_dashboard_chart_supports_month_year_and_all_time_periods(session: Session) -> None:
    now = datetime(2026, 7, 23, 12, 0, 0)
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "old-session", "1", datetime(2025, 5, 2, 12, 0, 0))
    record_action(usage, REAL_OWNER, "current-session", "1", now)
    session.commit()

    month_snapshot = usage.dashboard("real", now, period="month")
    year_snapshot = usage.dashboard("real", now, period="year")
    all_time_snapshot = usage.dashboard("real", now, period="all")
    month = month_snapshot.daily
    year = year_snapshot.daily
    all_time = all_time_snapshot.daily

    assert len(month) == 30
    assert (month[0].day, month[-1].day, sum(point.actions for point in month)) == (
        date(2026, 6, 24),
        date(2026, 7, 23),
        1,
    )
    assert len(year) == 12
    assert (year[0].day, year[-1].day, sum(point.actions for point in year)) == (
        date(2025, 8, 1),
        date(2026, 7, 1),
        1,
    )
    assert (all_time[0].day, all_time[-1].day, sum(point.actions for point in all_time)) == (
        date(2025, 5, 1),
        date(2026, 7, 1),
        2,
    )
    assert month_snapshot.totals.actions == year_snapshot.totals.actions == 1
    assert all_time_snapshot.totals.actions == 2


def test_chart_series_carry_every_selectable_metric(session: Session) -> None:
    played = datetime(2026, 7, 22, 12, 0, 0)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "session", "1", "real", played)
    record_action(usage, REAL_OWNER, "session", "2", played + timedelta(minutes=1))
    games = GameRepository(session)
    game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(game.id, REAL_OWNER, game.revision, ("e2e4", "e7e5"))
    session.add(PuzzleAttemptRow(owner_key=REAL_OWNER, puzzle_id="opened", revision=1, created_at=played))
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

    assert (day.launches, day.actions, day.users, day.sessions, day.new_users) == (1, 1, 1, 1, 1)
    assert (day.player_moves, day.engaged_games, day.puzzle_plays) == (1, 1, 1)
    assert (bucket.launches, bucket.actions, bucket.users, bucket.sessions) == (1, 1, 1, 1)
    assert (bucket.player_moves, bucket.engaged_games, bucket.puzzle_plays) == (1, 1, 1)
    assert month[date(2026, 7, 21)] == DailyUsage(date(2026, 7, 21))


def test_opened_puzzle_counts_without_a_move_and_reopening_does_not_add_one(session: Session) -> None:
    opened = datetime(2026, 7, 22, 12)
    usage = UsageRepository(session)
    usage.record_request(REAL_OWNER, "skill", "session", "1", "real", opened)
    puzzles = PuzzleRepository(session)
    puzzles.start_attempt(REAL_OWNER, "opened")
    puzzles.start_attempt(REAL_OWNER, "opened")
    attempt = session.get(PuzzleAttemptRow, (REAL_OWNER, "opened"))
    assert attempt is not None
    attempt.created_at = opened
    session.commit()

    month = usage.dashboard("real", opened, period="month")
    all_time = usage.dashboard("real", opened, period="all")

    assert month.totals.puzzle_plays == all_time.totals.puzzle_plays == 1
    assert sum(day.puzzle_plays for day in month.daily) == 1


def test_new_and_returning_users_split_the_active_ones(session: Session) -> None:
    newcomer = "c" * 64
    first = datetime(2026, 7, 21, 12, 0, 0)
    second = datetime(2026, 7, 22, 12, 0, 0)
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "first-visit", "1", first)
    record_action(usage, REAL_OWNER, "second-visit", "2", second)
    usage.record_request(newcomer, "skill", "only-visit", "1", "real", second)
    record_action(usage, newcomer, "only-visit", "2", second + timedelta(minutes=1))
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
    record_action(usage, REAL_OWNER, "june", "1", datetime(2026, 6, 30, 20, 59, 59))
    record_action(usage, REAL_OWNER, "july", "1", datetime(2026, 6, 30, 21, 0, 0))
    record_action(usage, REAL_OWNER, "before-midnight", "1", datetime(2026, 7, 23, 20, 59, 59))
    record_action(usage, REAL_OWNER, "after-midnight", "1", datetime(2026, 7, 23, 21, 0, 0))
    session.commit()

    month = usage.dashboard("real", datetime(2026, 7, 23, 21, 30, 0), period="month").daily
    all_time = usage.dashboard("real", datetime(2026, 7, 23, 21, 30, 0), period="all").daily

    daily_actions = {point.day: point.actions for point in month}
    monthly_actions = {point.day: point.actions for point in all_time}
    assert month[-1].day == date(2026, 7, 24)
    assert (daily_actions[date(2026, 7, 23)], daily_actions[date(2026, 7, 24)]) == (1, 1)
    assert (monthly_actions[date(2026, 6, 1)], monthly_actions[date(2026, 7, 1)]) == (1, 3)


def test_every_recordable_command_kind_fits_the_column() -> None:
    """A longer command value would fail the insert, not shorten the usage row."""
    limit = UsageRequestRow.__table__.columns["command_kind"].type.length
    recordable = [kind.value for kind in CommandKind]

    assert [value for value in recordable if len(value) > limit] == []
