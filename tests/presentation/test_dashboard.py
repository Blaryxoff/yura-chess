from __future__ import annotations

from datetime import date, datetime, timedelta

from yura_chess.presentation.dashboard import DASHBOARD_CSS, render_dashboard
from yura_chess.storage.usage_repository import DailyUsage, DashboardSnapshot, UsageTotals


def snapshot() -> DashboardSnapshot:
    totals = UsageTotals(120, 14, 32, 18, 7, 41, 2, 3)
    start = date(2026, 6, 24)
    daily = tuple(DailyUsage(start + timedelta(days=offset), requests=offset * 3) for offset in range(30))
    return DashboardSnapshot("real", "month", datetime(2026, 7, 23, 12, 0, 0), totals, daily)


def test_dashboard_is_aggregate_responsive_and_explains_pseudonymous_users() -> None:
    html = render_dashboard(snapshot())

    assert ">Статистика</h2>" in html
    assert "необратимый HMAC-ключ" in html
    assert "120" in html
    assert 'id="statistics"' in html
    assert 'href="/?period=month#statistics"' in html
    assert 'href="/?period=year#statistics"' in html
    assert 'rel="nofollow"' in html
    assert 'aria-label="Период статистики"' in html
    assert "Реальные" not in html
    assert "Тесты" not in html
    assert html.count('class="stats-cards"') == 1
    assert 'data-count="120"' in html
    assert "--delay:0ms" in html
    assert "overflow-y: hidden" in DASHBOARD_CSS
    assert "top: calc(100% + 10px)" in DASHBOARD_CSS
    assert "Запросы по дням · 30 дней" in html
    assert "owner_key" not in html
    assert "session_key" not in html
