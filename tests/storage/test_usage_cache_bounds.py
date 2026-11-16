from datetime import date, datetime

from sqlalchemy import event
from sqlalchemy.orm import Session

from yura_chess.storage.usage_repository import UsageRepository


def test_historical_queries_stop_at_exclusive_moscow_day_boundary(session: Session) -> None:
    usage = UsageRepository(session)
    for owner, session_id, moment in (
        ("a" * 64, "before", datetime(2026, 10, 31, 20, 59, 59)),
        ("b" * 64, "after", datetime(2026, 10, 31, 21)),
    ):
        usage.record_request(
            owner,
            "skill",
            session_id,
            "1",
            "real",
            moment,
            command_kind="help",
            routing_outcome="handled",
        )
    session.commit()

    totals = usage._totals("real", None, before=datetime(2026, 10, 31, 21))
    buckets = usage._buckets("real", "DATE({moment})", None, before=datetime(2026, 10, 31, 21))

    assert (totals.launches, totals.actions, totals.users, totals.sessions) == (1, 1, 1, 1)
    assert list(buckets) == [date(2026, 10, 31)]
    assert buckets[date(2026, 10, 31)]["actions"] == 1


def test_chart_history_is_not_rescanned_for_each_request(session: Session) -> None:
    usage = UsageRepository(session)
    usage.record_request("a" * 64, "skill", "legacy", "1", "real", datetime(2026, 1, 1))
    usage.record_request(
        "a" * 64,
        "skill",
        "current",
        "1",
        "real",
        datetime(2026, 11, 16),
        command_kind="help",
        routing_outcome="handled",
    )
    session.commit()
    connection = session.connection()
    queries: list[tuple[str, object]] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        queries.append((statement, parameters))

    event.listen(connection, "before_cursor_execute", capture)
    try:
        buckets = usage._buckets("real", "DATE({moment})", date(2026, 11, 1))
    finally:
        event.remove(connection, "before_cursor_execute", capture)

    assert buckets[date(2026, 11, 16)]["new_users"] == 1
    for statement, parameters in queries:
        plan = connection.exec_driver_sql("EXPLAIN " + statement, parameters).mappings().all()
        assert all(row["select_type"] != "DEPENDENT SUBQUERY" for row in plan), plan
