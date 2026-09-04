from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pytest

from yura_chess.presentation.dashboard import DASHBOARD_CSS, render_dashboard
from yura_chess.presentation.website import SITE_CSS
from yura_chess.storage.usage_repository import DailyUsage, DashboardSnapshot, UsageTotals


def snapshot() -> DashboardSnapshot:
    totals = UsageTotals(120, 14, 32, 18, 7, 41, 2, 3)
    start = date(2026, 6, 24)
    daily = tuple(
        DailyUsage(
            start + timedelta(days=offset),
            requests=offset * 3,
            users=offset,
            new_users=offset + 6,
            returning_users=offset + 7,
            sessions=offset * 2,
            games=offset + 1,
            player_moves=offset * 4,
            engaged_games=offset + 2,
            puzzle_attempts=offset * 5,
        )
        for offset in range(30)
    )
    return DashboardSnapshot("real", "month", datetime(2026, 7, 23, 12, 0, 0), totals, daily)


def test_dashboard_is_aggregate_responsive_and_explains_pseudonymous_users() -> None:
    html = render_dashboard(snapshot())

    assert ">Статистика</h2>" in html
    assert "необратимый HMAC-ключ" in html
    assert "120" in html
    assert 'id="statistics"' in html
    assert 'href="/statistics?period=month&amp;metric=engaged_games#statistics"' in html
    assert 'href="/statistics?period=year&amp;metric=engaged_games#statistics"' in html
    assert 'rel="nofollow"' in html
    assert 'aria-label="Период статистики"' in html
    assert "Реальные" not in html
    assert "Тесты" not in html
    assert html.count('class="stats-cards"') == 1
    assert 'data-count="120"' in html
    assert "--delay:0ms" in html
    assert "overflow-y: hidden" in DASHBOARD_CSS
    assert "top: calc(100% + 10px)" in DASHBOARD_CSS
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in DASHBOARD_CSS
    assert "Партии с ходом по дням · 30 дней" in html
    assert "owner_key" not in html
    assert "session_key" not in html


def test_chart_offers_every_metric_and_keeps_the_period_while_switching() -> None:
    html = render_dashboard(snapshot(), "player_moves")

    assert 'name="metric"' in html
    assert '<input type="hidden" name="period" value="month">' in html
    assert 'value="player_moves" selected' in html
    assert [match.group(1) for match in re.finditer(r'<option value="([a-z_]+)"', html)] == [
        "engaged_games",
        "player_moves",
        "returning_users",
        "puzzle_attempts",
        "games",
        "sessions",
        "users",
        "new_users",
        "requests",
    ]
    assert 'href="/statistics?period=year&amp;metric=player_moves#statistics"' in html


@pytest.mark.parametrize(
    ("metric", "title", "column", "last_value"),
    [
        ("requests", "Запросы по дням · 30 дней", "Запросов", 87),
        ("users", "Пользователи по дням · 30 дней", "Пользователей", 29),
        ("new_users", "Новые пользователи по дням · 30 дней", "Новых", 35),
        ("returning_users", "Вернувшиеся по дням · 30 дней", "Вернувшихся", 36),
        ("sessions", "Сессии по дням · 30 дней", "Сессий", 58),
        ("player_moves", "Ходы игроков по дням · 30 дней", "Ходов", 116),
        ("games", "Новые партии по дням · 30 дней", "Партий", 30),
        ("engaged_games", "Партии с ходом по дням · 30 дней", "Партий с ходом", 31),
        ("puzzle_attempts", "Шахматные задачи по дням · 30 дней", "Задач", 145),
    ],
)
def test_chart_bars_and_table_follow_the_chosen_metric(
    metric: str,
    title: str,
    column: str,
    last_value: int,
) -> None:
    html = render_dashboard(snapshot(), metric)  # type: ignore[arg-type]

    assert f"<h3>{title}</h3>" in html
    assert f"<caption>{title}</caption>" in html
    assert f'<th scope="col">{column}</th>' in html
    assert f'<div class="stats-bar-value">{last_value}</div>' in html
    assert f'<tr><th scope="row">23.07.2026</th><td>{last_value}</td></tr>' in html


@pytest.mark.parametrize(
    ("value", "user_noun", "expected_labels"),
    [
        (
            1,
            "пользователь",
            (
                "запрос",
                "сессия",
                "ход игрока",
                "новая партия",
                "партия с ходом",
                "завершённая партия",
                "шахматная задача",
            ),
        ),
        (
            2,
            "пользователя",
            (
                "запроса",
                "сессии",
                "хода игроков",
                "новые партии",
                "партии с ходом",
                "завершённые партии",
                "шахматные задачи",
            ),
        ),
        (
            5,
            "пользователей",
            (
                "запросов",
                "сессий",
                "ходов игроков",
                "новых партий",
                "партий с ходом",
                "завершённых партий",
                "шахматных задач",
            ),
        ),
        (
            11,
            "пользователей",
            (
                "запросов",
                "сессий",
                "ходов игроков",
                "новых партий",
                "партий с ходом",
                "завершённых партий",
                "шахматных задач",
            ),
        ),
        (
            21,
            "пользователь",
            (
                "запрос",
                "сессия",
                "ход игрока",
                "новая партия",
                "партия с ходом",
                "завершённая партия",
                "шахматная задача",
            ),
        ),
    ],
)
def test_dashboard_uses_russian_plural_forms(
    value: int,
    user_noun: str,
    expected_labels: tuple[str, ...],
) -> None:
    totals = UsageTotals(value, value, value, value, value, value, value, value)
    html = render_dashboard(DashboardSnapshot("real", "month", datetime(2026, 7, 23, 12, 0, 0), totals, ()))

    assert '<div class="stats-label"><button' in html
    assert f'aria-expanded="false">{user_noun}</button>' in html
    for label in expected_labels:
        assert f'<div class="stats-label">{label}</div>' in html


def test_content_headings_have_space_from_the_preceding_block() -> None:
    assert "section > h2:not(:first-child) { margin-top: 26px; }" in SITE_CSS
