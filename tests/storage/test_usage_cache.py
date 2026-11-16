from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.models import GameMoveRow, GameRow, PuzzleAttemptRow, UsageDashboardCacheRow
from yura_chess.storage.usage_repository import UsageRepository

REAL_OWNER = "a" * 64
NEW_OWNER = "c" * 64
TEST_OWNER = "d" * 64
NOW = datetime(2026, 7, 23, 21, 30)
YESTERDAY = datetime(2026, 7, 23, 20, 45)
TODAY = datetime(2026, 7, 23, 21, 5)


def record_action(
    usage: UsageRepository,
    owner: str,
    session_id: str,
    message_id: str,
    when: datetime,
    source: str = "real",
) -> None:
    usage.record_request(
        owner,
        "skill",
        session_id,
        message_id,
        source,
        when,
        command_kind="help",
        routing_outcome="handled",
    )


def seed_dashboard(session: Session) -> None:
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "returning", "old", datetime(2025, 5, 2, 10))
    record_action(usage, REAL_OWNER, "returning", "yesterday", YESTERDAY)
    record_action(usage, REAL_OWNER, "returning", "today", TODAY)
    record_action(usage, NEW_OWNER, "new", "today", TODAY)
    record_action(usage, TEST_OWNER, "synthetic", "yesterday", YESTERDAY, source="test")

    games = GameRepository(session)
    game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(game.id, REAL_OWNER, game.revision, ("e2e4", "e7e5", "g1f3"))
    moves = session.scalars(select(GameMoveRow).where(GameMoveRow.game_id == game.id).order_by(GameMoveRow.ply)).all()
    moves[0].created_at = YESTERDAY
    moves[1].created_at = YESTERDAY
    moves[2].created_at = TODAY
    session.add(PuzzleAttemptRow(owner_key=REAL_OWNER, puzzle_id="cache-old", created_at=datetime(2025, 5, 2, 10)))


@pytest.mark.parametrize("source", ["real", "test", "all"])
@pytest.mark.parametrize("period", ["month", "year", "all"])
def test_cached_dashboard_matches_uncached_for_sources_and_periods(session: Session, source: str, period: str) -> None:
    seed_dashboard(session)
    session.commit()
    usage = UsageRepository(session)

    cached = usage.cached_dashboard(source, NOW, period=period)
    uncached = usage.dashboard(source, NOW, period=period)

    assert cached == uncached


def test_today_delta_deduplicates_prior_owners_sessions_and_games(session: Session) -> None:
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "shared-session", "yesterday", YESTERDAY)
    games = GameRepository(session)
    game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(game.id, REAL_OWNER, game.revision, ("e2e4",))
    move = session.scalars(select(GameMoveRow).where(GameMoveRow.game_id == game.id)).one()
    move.created_at = YESTERDAY
    session.commit()

    usage.cached_dashboard("real", NOW, period="all")
    session.commit()

    record_action(usage, REAL_OWNER, "shared-session", "today", TODAY)
    record_action(usage, NEW_OWNER, "new-session", "today", TODAY)
    state = games.load(game.id, REAL_OWNER)
    games.append_moves(game.id, REAL_OWNER, state.revision, ("e7e5", "g1f3"))
    game_moves = session.scalars(
        select(GameMoveRow).where(GameMoveRow.game_id == game.id).order_by(GameMoveRow.ply)
    ).all()
    game_moves[-2].created_at = TODAY
    game_moves[-1].created_at = TODAY
    session.commit()

    cached = UsageRepository(session).cached_dashboard("real", NOW, period="all")
    uncached = UsageRepository(session).dashboard("real", NOW, period="all")

    assert cached.totals == uncached.totals
    assert (cached.totals.actions, cached.totals.users, cached.totals.sessions) == (3, 2, 2)
    assert (cached.totals.engaged_games, cached.totals.player_moves) == (1, 2)


@pytest.mark.parametrize("period,expected_launches", [("month", 0), ("year", 0), ("all", 1)])
def test_today_delta_counts_entities_whose_only_prior_activity_is_outside_period(
    session: Session, period: str, expected_launches: int
) -> None:
    usage = UsageRepository(session)
    outside_period = datetime(2024, 4, 1, 12)
    record_action(usage, REAL_OWNER, "long-lived-session", "old", outside_period)
    games = GameRepository(session)
    game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(game.id, REAL_OWNER, game.revision, ("e2e4",))
    session.scalars(select(GameMoveRow).where(GameMoveRow.game_id == game.id)).one().created_at = outside_period

    record_action(usage, REAL_OWNER, "long-lived-session", "today", TODAY)
    state = games.load(game.id, REAL_OWNER)
    games.append_moves(game.id, REAL_OWNER, state.revision, ("e7e5", "g1f3"))
    moves = session.scalars(select(GameMoveRow).where(GameMoveRow.game_id == game.id).order_by(GameMoveRow.ply)).all()
    moves[1].created_at = TODAY
    moves[2].created_at = TODAY
    session.commit()

    cached = UsageRepository(session).cached_dashboard("real", NOW, period=period)
    uncached = UsageRepository(session).dashboard("real", NOW, period=period)

    assert cached.totals == uncached.totals
    assert (cached.totals.users, cached.totals.sessions, cached.totals.engaged_games) == (1, 1, 1)
    assert cached.totals.launches == expected_launches


def test_finished_games_stay_live_when_a_game_finishes_after_cache_build(session: Session) -> None:
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "finished-game", "1", YESTERDAY)
    games = GameRepository(session)
    game = games.create_game(REAL_OWNER, PlayerColor.WHITE)
    games.append_moves(game.id, REAL_OWNER, game.revision, ("e2e4",))
    move = session.scalars(select(GameMoveRow).where(GameMoveRow.game_id == game.id)).one()
    move.created_at = YESTERDAY
    row = session.get(GameRow, game.id)
    assert row is not None
    row.updated_at = YESTERDAY
    session.commit()

    before = usage.cached_dashboard("real", NOW, period="all")
    assert before.totals.finished_games == 0
    session.commit()

    state = games.load(game.id, REAL_OWNER)
    games.append_moves(game.id, REAL_OWNER, state.revision, (), status=GameStatus.FINISHED)
    row = session.get(GameRow, game.id)
    assert row is not None
    row.updated_at = TODAY
    session.commit()

    cached = UsageRepository(session).cached_dashboard("real", NOW, period="all")
    uncached = UsageRepository(session).dashboard("real", NOW, period="all")

    assert cached.totals.finished_games == 1
    assert cached.totals == uncached.totals


def test_same_day_cache_reuse_skips_historical_totals_after_new_session_commit(
    session: Session, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_action(UsageRepository(session), REAL_OWNER, "same-day", "yesterday", YESTERDAY)
    UsageRepository(session).cached_dashboard("real", NOW, period="all")
    session.commit()
    original = UsageRepository._totals
    original_buckets = UsageRepository._buckets
    historical_bounds: list[datetime | None] = []
    bucket_starts: list[object] = []

    def spy_totals(
        repository: UsageRepository,
        source: str,
        cutoff: datetime | None,
        *,
        before: datetime | None = None,
    ):
        historical_bounds.append(before)
        if before is not None:
            pytest.fail("historical totals were recomputed after a same-day cache was stored")
        return original(repository, source, cutoff, before=before)

    def spy_buckets(
        repository: UsageRepository,
        source: str,
        bucket: str,
        start,
        *,
        before: datetime | None = None,
    ):
        if before is not None or start is None:
            pytest.fail("closed chart buckets were recomputed after a same-day cache was stored")
        bucket_starts.append(start)
        return original_buckets(repository, source, bucket, start, before=before)

    with session_factory() as later_session:
        record_action(UsageRepository(later_session), REAL_OWNER, "same-day", "today", TODAY)
        record_action(UsageRepository(later_session), NEW_OWNER, "same-day-new", "today", TODAY)
        later_session.commit()
        uncached = UsageRepository(later_session).dashboard("real", NOW, period="all")
        monkeypatch.setattr(UsageRepository, "_totals", spy_totals)
        monkeypatch.setattr(UsageRepository, "_buckets", spy_buckets)
        cached = UsageRepository(later_session).cached_dashboard("real", NOW, period="all")

    assert cached.totals == uncached.totals
    assert cached.totals.actions == 3
    assert cached.totals.users == 2
    assert all(bound is None for bound in historical_bounds)
    assert bucket_starts


def test_next_moscow_day_rebuild_includes_late_historical_correction(session: Session) -> None:
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "existing", "yesterday", YESTERDAY)
    session.commit()
    end_of_july = datetime(2026, 7, 31, 20, 30)

    first = usage.cached_dashboard("real", end_of_july, period="all")
    session.commit()
    assert first.totals.actions == 1

    record_action(usage, NEW_OWNER, "late-history", "late", datetime(2026, 5, 2, 9))
    session.commit()
    same_day = UsageRepository(session).cached_dashboard("real", end_of_july, period="all")
    assert same_day.totals.actions == 1

    next_moscow_day = end_of_july + timedelta(hours=2)
    cached = UsageRepository(session).cached_dashboard("real", next_moscow_day, period="all")
    uncached = UsageRepository(session).dashboard("real", next_moscow_day, period="all")

    assert cached == uncached
    assert cached.totals.actions == 2


def test_production_dashboard_api_persists_and_reuses_cache_row(session: Session) -> None:
    usage = UsageRepository(session)

    first = usage.dashboard("real", period="all")
    session.commit()
    cache_rows = session.scalars(select(UsageDashboardCacheRow)).all()
    assert len(cache_rows) == 1
    assert (cache_rows[0].source, cache_rows[0].period) == ("real", "all")

    second = UsageRepository(session).dashboard("real", period="all")
    session.commit()

    assert second.totals == first.totals
    assert len(session.scalars(select(UsageDashboardCacheRow)).all()) == 1


def test_schema_version_mismatch_rebuilds_the_cached_snapshot(session: Session) -> None:
    usage = UsageRepository(session)
    record_action(usage, REAL_OWNER, "versioned", "1", YESTERDAY)
    session.commit()
    usage.cached_dashboard("real", NOW, period="all")
    session.commit()

    cached = session.get(UsageDashboardCacheRow, ("real", "all"))
    assert cached is not None
    cached.schema_version = 0
    cached.totals = {"actions": 999}
    session.commit()

    refreshed = UsageRepository(session).cached_dashboard("real", NOW, period="all")
    uncached = UsageRepository(session).dashboard("real", NOW, period="all")

    assert refreshed == uncached
    rebuilt = session.get(UsageDashboardCacheRow, ("real", "all"))
    assert rebuilt is not None
    assert rebuilt.schema_version == 1
