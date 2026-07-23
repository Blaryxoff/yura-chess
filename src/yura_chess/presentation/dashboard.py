"""Public aggregate usage dashboard with no user-level data."""

# ruff: noqa: E501 - the inline CSS/HTML remains readable as browser-native lines

from __future__ import annotations

from zoneinfo import ZoneInfo

from yura_chess.storage.usage_repository import DashboardSnapshot, UsageTotals

_PERIOD_LABELS = {"month": "Месяц", "year": "Год", "all": "Всё время"}
_TOTAL_TITLES = {
    "month": "Последние 30 дней",
    "year": "Последние 12 месяцев",
    "all": "За всё время",
}
_CHART_TITLES = {
    "month": "Запросы по дням · 30 дней",
    "year": "Запросы по месяцам · 12 месяцев",
    "all": "Запросы по месяцам · всё время",
}

DASHBOARD_CSS = """
    .stats { scroll-margin-top: 20px; }
    .stats-top { display: flex; justify-content: space-between; align-items: end; gap: 24px; }
    .stats-kicker { color: var(--gold); font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
    .stats-muted { color: var(--muted); }
    .stats-tabs { display: flex; gap: 7px; flex-wrap: wrap; }
    .stats-tab {
      padding: 7px 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      text-decoration: none;
    }
    .stats-tab.active { border-color: var(--gold); background: var(--gold); color: #241d12; font-weight: 800; }
    .stats-panel { margin-top: 18px; padding: 22px; border: 1px solid var(--line); border-radius: 18px; background: #1d1c19; }
    .stats-panel h3 { margin: 0 0 16px; font-size: 20px; }
    .stats-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .stats-card { min-width: 0; padding: 18px; border: 1px solid var(--line); border-radius: 15px; background: #171613; }
    .stats-value { color: var(--gold); font-size: clamp(28px, 4vw, 42px); font-weight: 850; line-height: 1; }
    .stats-label { margin-top: 8px; color: var(--muted); overflow-wrap: anywhere; }
    .stats-chart { height: 220px; display: flex; align-items: end; gap: 8px; padding-top: 18px; overflow-x: auto; }
    .stats-day { height: 180px; min-width: 38px; flex: 1; display: flex; flex-direction: column; justify-content: end; align-items: center; gap: 5px; }
    .stats-bar { width: min(30px, 80%); background: linear-gradient(#f2d38f, #a8792e); border-radius: 7px 7px 3px 3px; }
    .stats-bar-value { color: var(--muted); font-size: 12px; }
    .stats-day time { margin-top: 9px; color: var(--muted); font-size: 11px; transform: rotate(-42deg); }
    .stats-note { display: grid; grid-template-columns: auto 1fr; gap: 13px; align-items: start; }
    .stats-shield { color: var(--gold); font-size: 28px; }
    @media (max-width: 850px) {
      .stats-top { align-items: start; flex-direction: column; }
      .stats-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 520px) {
      .stats-cards { grid-template-columns: minmax(0, 1fr); }
      .stats-card { padding: 14px; }
    }
"""


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    peak = max((point.requests for point in snapshot.daily), default=1) or 1
    date_format = "%d.%m" if snapshot.period == "month" else "%m.%y"
    bars = "".join(
        f"""<div class="stats-day" title="{point.day:{date_format}}: {point.requests} запросов">
          <div class="stats-bar-value">{point.requests}</div>
          <div class="stats-bar" style="height:{max(4, round(point.requests / peak * 150))}px"></div>
          <time datetime="{point.day.isoformat()}">{point.day:{date_format}}</time>
        </div>"""
        for point in snapshot.daily
    )
    periods = "".join(
        f'<a class="stats-tab{" active" if key == snapshot.period else ""}" rel="nofollow" href="/?period={key}#statistics">{label}</a>'
        for key, label in _PERIOD_LABELS.items()
    )
    generated = snapshot.generated_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Moscow"))
    return f"""<section id="statistics" class="stats">
      <div class="stats-top"><div><div class="stats-kicker">Использование навыка</div><h2>Статистика</h2><div class="stats-muted">Обновлено {generated:%d.%m.%Y %H:%M} МСК</div></div><nav class="stats-tabs" aria-label="Период статистики">{periods}</nav></div>
      <div class="stats-panel"><h3>{_TOTAL_TITLES[snapshot.period]}</h3>{_cards(snapshot.totals)}</div>
      <div class="stats-panel"><h3>{_CHART_TITLES[snapshot.period]}</h3><div class="stats-chart" role="img" aria-label="Число запросов за выбранный период">{bars}</div></div>
      <div class="stats-panel stats-note"><div class="stats-shield">◈</div><div><strong>Что значит «пользователь»?</strong><br><span class="stats-muted">Это стабильный необратимый HMAC-ключ. Исходный Alice ID не сохраняется. Запросы и сессии в этой статистике также представлены только хешами. Автоматические проверки помечаются как test до хеширования и не учитываются в публичной статистике.</span></div></div>
    </section>"""


def _cards(totals: UsageTotals) -> str:
    values = (
        (totals.users, "активных пользователей"),
        (totals.requests, "запросов"),
        (totals.sessions, "сессий"),
        (totals.player_moves, "ходов игроков"),
        (totals.games, "новых партий"),
        (totals.engaged_games, "партий с ходом"),
        (totals.finished_games, "завершённых партий"),
        (totals.puzzle_attempts, "шахматных задач"),
    )
    return (
        '<div class="stats-cards">'
        + "".join(
            f'<div class="stats-card"><div class="stats-value">{value:,}</div><div class="stats-label">{label}</div></div>'
            for value, label in values
        )
        + "</div>"
    )
