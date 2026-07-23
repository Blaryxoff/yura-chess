from __future__ import annotations

from datetime import date, datetime, timedelta

from yura_chess.presentation.dashboard import render_dashboard
from yura_chess.storage.usage_repository import DailyUsage, DashboardSnapshot, UsageTotals


def snapshot() -> DashboardSnapshot:
    totals = UsageTotals(120, 14, 32, 18, 7, 41, 2, 3)
    start = date(2026, 7, 10)
    daily = tuple(DailyUsage(start + timedelta(days=offset), requests=offset * 3) for offset in range(14))
    return DashboardSnapshot("real", datetime(2026, 7, 23, 12, 0, 0), totals, totals, totals, daily)


def test_dashboard_is_aggregate_responsive_and_explains_pseudonymous_users() -> None:
    html = render_dashboard(snapshot())

    assert "Статистика навыка" in html
    assert "необратимый HMAC-ключ" in html
    assert "120" in html
    assert 'href="/dashboard?source=test"' in html
    assert "@media(max-width:650px)" in html
    assert "grid-template-columns:minmax(0,1fr)" in html
    assert "owner_key" not in html
    assert "session_key" not in html
