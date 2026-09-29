from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session

from yura_chess.storage.feedback_repository import FeedbackRepository
from yura_chess.storage.models import ReviewClickDailyRow, UsageRequestRow, UsageUserRow
from yura_chess.storage.usage_repository import UsageRepository, request_key

REAL_OWNER = "a" * 64
TEST_OWNER = "b" * 64


def test_prompt_marker_is_owner_scoped_and_idempotent(session: Session) -> None:
    UsageRepository(session).record_request(REAL_OWNER, "skill", "session", "1", "real", datetime(2026, 9, 28))
    repository = FeedbackRepository(session)
    key = request_key("skill", "session", "1")

    assert repository.record_prompt(TEST_OWNER, key, "automatic") is False
    assert repository.record_prompt(REAL_OWNER, "c" * 64, "automatic") is False
    assert repository.record_prompt(REAL_OWNER, key, "automatic") is True
    assert repository.record_prompt(REAL_OWNER, key, "requested") is False
    assert session.get(UsageRequestRow, key).review_prompt_kind == "automatic"
    user = session.get(UsageUserRow, REAL_OWNER)
    assert user.review_prompt_count == 0
    assert user.review_prompts_disabled is False


def test_report_excludes_test_users_and_uses_inclusive_utc_dates(session: Session) -> None:
    usage = UsageRepository(session)
    repository = FeedbackRepository(session)
    for owner, source, message, moment, kind in (
        (REAL_OWNER, "real", "1", datetime(2026, 9, 27, 23, 59, 59), "automatic"),
        (REAL_OWNER, "real", "2", datetime(2026, 9, 28), "automatic"),
        (REAL_OWNER, "real", "3", datetime(2026, 9, 28, 23, 59, 59), "requested"),
        (REAL_OWNER, "real", "4", datetime(2026, 9, 29), "automatic"),
        (TEST_OWNER, "test", "5", datetime(2026, 9, 28), "requested"),
    ):
        usage.record_request(owner, "skill", "session", message, source, moment)
        assert repository.record_prompt(owner, request_key("skill", "session", message), kind)

    report = repository.report(date(2026, 9, 28), date(2026, 9, 28))

    assert report.prompts == {"automatic": 1, "requested": 1}
    assert report.clicks == {"skill": 0, "guide": 0, "landing": 0}


def test_test_reclassification_excludes_previously_marked_real_requests(session: Session) -> None:
    usage = UsageRepository(session)
    repository = FeedbackRepository(session)
    usage.record_request(REAL_OWNER, "skill", "session", "1", "real", datetime(2026, 9, 28))
    repository.record_prompt(REAL_OWNER, request_key("skill", "session", "1"), "requested")
    usage.record_request(REAL_OWNER, "skill", "test-session", "2", "test", datetime(2026, 9, 28))

    assert repository.report(date(2026, 9, 28), date(2026, 9, 28)).prompts == {"automatic": 0, "requested": 0}


def test_clicks_are_utc_daily_atomic_aggregates_without_person_identifiers(session: Session) -> None:
    repository = FeedbackRepository(session)
    moment = datetime(2026, 9, 29, 1, tzinfo=timezone(timedelta(hours=3)))
    repository.record_click("guide", moment)
    repository.record_click("guide", datetime(2026, 9, 28, 22, tzinfo=UTC))
    repository.record_click("skill", datetime(2026, 9, 28))
    repository.record_click("landing", datetime(2026, 9, 29))

    assert repository.report(date(2026, 9, 28), date(2026, 9, 28)).clicks == {
        "skill": 1,
        "guide": 2,
        "landing": 0,
    }
    assert len(session.scalars(select(ReviewClickDailyRow)).all()) == 3
    assert {column.name for column in inspect(ReviewClickDailyRow).columns} == {"day", "source", "clicks"}


def test_published_snapshots_are_manual_distinct_and_replaceable(session: Session) -> None:
    repository = FeedbackRepository(session)
    repository.record_published("dialogs", date(2026, 9, 1), 6, 2)
    repository.record_published("dialogs", date(2026, 9, 21), 7, 3)
    repository.record_published("dialogs", date(2026, 9, 28), 8, 4)
    repository.record_published("dialogs", date(2026, 9, 28), 7, 2)
    repository.record_published("dialogs", date(2026, 9, 29), 9, 5)
    repository.record_published("browser", date(2026, 9, 28), 1, 1)

    dialogs, browser = repository.report(date(2026, 9, 28), date(2026, 9, 28)).published

    assert dialogs.latest.day == date(2026, 9, 28)
    assert (dialogs.latest.ratings, dialogs.latest.reviews) == (7, 2)
    assert dialogs.previous.day == date(2026, 9, 21)
    assert dialogs.latest.reviews - dialogs.previous.reviews == -1
    assert browser.latest.reviews == 1
    assert browser.previous is None


@pytest.mark.parametrize("ratings,reviews", [(-1, 0), (1, -1), (1, 2)])
def test_published_totals_reject_invalid_counts(session: Session, ratings: int, reviews: int) -> None:
    with pytest.raises(ValueError, match="0 <= reviews <= ratings"):
        FeedbackRepository(session).record_published("dialogs", date(2026, 9, 28), ratings, reviews)


def test_report_rejects_inverted_dates(session: Session) -> None:
    with pytest.raises(ValueError, match="since must be on or before until"):
        FeedbackRepository(session).report(date(2026, 9, 29), date(2026, 9, 28))


def test_migration_downgrade_and_legacy_prompt_backfill(database_engine: Engine) -> None:
    moment = datetime(2026, 9, 28)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO usage_users "
                "(owner_key, traffic_source, first_seen_at, last_seen_at, review_prompted_at) "
                "VALUES (:owner_key, 'real', :moment, :moment, :prompted_at)"
            ),
            [
                {"owner_key": REAL_OWNER, "moment": moment, "prompted_at": moment},
                {"owner_key": TEST_OWNER, "moment": moment, "prompted_at": None},
            ],
        )
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    try:
        command.downgrade(config, "0018")
        schema = inspect(database_engine)
        assert "review_click_daily" not in schema.get_table_names()
        assert "published_review_snapshots" not in schema.get_table_names()
        assert "review_prompt_kind" not in {column["name"] for column in schema.get_columns("usage_requests")}
        user_columns = {column["name"] for column in schema.get_columns("usage_users")}
        assert "review_prompted_at" in user_columns
        assert "review_prompt_count" not in user_columns
        assert "review_prompts_disabled" not in user_columns
        command.upgrade(config, "head")
        with database_engine.connect() as connection:
            counts = dict(connection.execute(text("SELECT owner_key, review_prompt_count FROM usage_users")).all())
            disabled = connection.execute(text("SELECT review_prompts_disabled FROM usage_users")).scalars().all()
        assert counts == {REAL_OWNER: 1, TEST_OWNER: 0}
        assert disabled == [0, 0]
    finally:
        command.upgrade(config, "head")
