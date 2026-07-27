"""Public aggregate usage dashboard with no user-level data."""

# ruff: noqa: E501 - the inline CSS/HTML remains readable as browser-native lines

from __future__ import annotations

from zoneinfo import ZoneInfo

from yura_chess.presentation.russian import plural_form
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
      transition: transform 420ms var(--spring), border-color 180ms ease, color 180ms ease, background 180ms ease;
    }
    .stats-tab.active { border-color: var(--gold); background: var(--gold); color: #241d12; font-weight: 800; }
    .stats-panel { margin-top: 18px; padding: 22px; border: 1px solid var(--line); border-radius: 18px; background: #1d1c19; }
    .stats-panel h3 { margin: 0 0 16px; font-size: 20px; }
    .stats-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .stats-card { position: relative; min-width: 0; padding: 18px; border: 1px solid var(--line); border-radius: 15px; background: #171613; }
    .stats-value { color: var(--gold); font-size: clamp(28px, 4vw, 42px); font-weight: 850; line-height: 1; }
    .stats-label { margin-top: 8px; color: var(--muted); overflow-wrap: anywhere; }
    .stats-chart {
      height: 260px;
      display: flex;
      align-items: stretch;
      gap: 8px;
      box-sizing: border-box;
      padding: 18px 52px 58px 12px;
      overflow-x: auto;
      overflow-y: hidden;
      overscroll-behavior-inline: contain;
      scroll-behavior: smooth;
      scrollbar-color: #8d682d #171613;
      scrollbar-width: thin;
    }
    .stats-day { position: relative; min-width: 38px; flex: 1; display: flex; flex-direction: column; justify-content: end; align-items: center; gap: 5px; }
    .stats-bar { width: min(30px, 80%); background: linear-gradient(#f2d38f, #a8792e); border-radius: 7px 7px 3px 3px; }
    .stats-bar-value { color: var(--muted); font-size: 12px; }
    .stats-day time {
      position: absolute;
      top: calc(100% + 10px);
      left: 50%;
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
      transform: rotate(-42deg);
      transform-origin: top left;
    }
    .has-motion .stats-card { opacity: 0; transform: translateY(18px) scale(.97); }
    .has-motion .stats-cards.is-visible .stats-card {
      animation: stats-pop-in 720ms var(--spring) both;
      animation-delay: var(--delay);
    }
    .has-motion .stats-bar { opacity: 0; transform: scaleY(0); transform-origin: bottom; }
    .has-motion .stats-chart.is-visible .stats-bar {
      animation: stats-bar-in 820ms var(--spring) both;
      animation-delay: var(--delay);
    }
    .has-motion .stats-chart.is-visible .stats-bar-value,
    .has-motion .stats-chart.is-visible time {
      animation: stats-label-in 420ms ease-out both;
      animation-delay: calc(var(--delay) + 220ms);
    }
    @keyframes stats-pop-in { to { opacity: 1; transform: none; } }
    @keyframes stats-bar-in { to { opacity: 1; transform: scaleY(1); } }
    @keyframes stats-label-in { from { opacity: 0; } to { opacity: 1; } }
    /* The hint stays unpositioned on purpose: an inline span around one word is a
       useless containing block, and anchoring the panel to it both mispositions it
       and traps its z-index inside a sibling card's stacking context. The card owns
       the geometry instead. */
    .visually-hidden {
      position: absolute;
      width: 1px;
      height: 1px;
      margin: -1px;
      padding: 0;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
      border: 0;
    }
    /* A real button, not a focusable span: the panel has to be dismissible and
       operable by keyboard and by touch, and only a button gets that for free. */
    .stats-hint {
      padding: 0;
      border: 0;
      border-bottom: 1px dashed #6d6555;
      background: none;
      color: inherit;
      font: inherit;
      cursor: help;
    }
    .stats-hint:focus-visible { outline: 2px solid var(--gold); outline-offset: 3px; border-radius: 3px; }
    .stats-tip {
      position: absolute;
      top: calc(100% + 12px);
      left: 12px;
      right: 12px;
      z-index: 5;
      padding: 13px 15px;
      border: 1px solid #4c4638;
      border-radius: 14px;
      background: #100f0d;
      color: var(--text);
      font-size: 14px;
      line-height: 1.5;
      text-align: left;
      box-shadow: 0 18px 40px #00000059;
      opacity: 0;
      visibility: hidden;
      transform: translateY(-6px) scale(.97);
      transform-origin: top center;
      transition: opacity 200ms ease, transform 420ms var(--spring), visibility 200ms;
    }
    .stats-tip strong { display: block; margin-bottom: 5px; color: var(--gold); }
    .stats-tip::after {
      content: "";
      position: absolute;
      bottom: 100%;
      left: 24px;
      border: 7px solid transparent;
      border-bottom-color: #4c4638;
    }
    /* Flipped by script only when the panel would not fit under the card. */
    .stats-card.tip-above .stats-tip {
      top: auto;
      bottom: calc(100% + 12px);
      transform: translateY(6px) scale(.97);
      transform-origin: bottom center;
    }
    .stats-card.tip-above .stats-tip::after {
      top: 100%;
      bottom: auto;
      border-bottom-color: transparent;
      border-top-color: #4c4638;
    }
    /* Hover is the mouse affordance; aria-expanded is the state the script owns,
       so keyboard, touch and Escape all go through the same switch. */
    .stats-hint:hover + .stats-tip,
    .stats-hint[aria-expanded="true"] + .stats-tip {
      opacity: 1;
      visibility: visible;
      transform: none;
    }
    /* The open card is lifted so the cards after it in source order — each its own
       stacking context while the reveal animation holds a transform — cannot paint
       over the panel. */
    .stats-card:hover,
    .stats-card:focus-within,
    .stats-card:has(.stats-hint[aria-expanded="true"]) { z-index: 4; }
    @media (hover: hover) {
      .stats-tab:hover { color: var(--gold); border-color: var(--gold); transform: translateY(-2px) scale(1.03); }
      .stats-tab.active:hover { color: #241d12; }
    }
    .stats-table caption { text-align: left; }
    @media (max-width: 850px) {
      .stats-top { align-items: start; flex-direction: column; }
      .stats-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 520px) {
      .stats-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
      .stats-card { padding: 12px; }
      .stats-card:first-child { grid-column: 1 / -1; }
      .stats-value { font-size: 28px; }
      .stats-label { font-size: 14px; line-height: 1.4; }
      .stats-chart { height: 230px; padding-right: 18px; }
    }
"""


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    peak = max((point.requests for point in snapshot.daily), default=1) or 1
    date_format = "%d.%m" if snapshot.period == "month" else "%m.%y"
    bars = "".join(
        f"""<div class="stats-day" style="--delay:{min(len(snapshot.daily) - index - 1, 20) * 28}ms">
          <div class="stats-bar-value">{point.requests}</div>
          <div class="stats-bar" style="height:{max(4, round(point.requests / peak * 150))}px"></div>
          <time datetime="{point.day.isoformat()}">{point.day:{date_format}}</time>
        </div>"""
        for index, point in enumerate(snapshot.daily)
    )
    # The bars are a picture of the table below them, so they are hidden outright
    # rather than labelled: role="img" flattens its children out of the
    # accessibility tree, which left the whole chart as one contentless label.
    rows = "".join(
        f'<tr><th scope="row">{point.day:%d.%m.%Y}</th><td>{point.requests}</td></tr>' for point in snapshot.daily
    )
    # Visually hidden rather than behind a disclosure: the audience this site is
    # built for should not have to open anything to reach what the chart shows.
    table = f"""<table class="stats-table visually-hidden">
        <caption>{_CHART_TITLES[snapshot.period]}</caption>
        <thead><tr><th scope="col">Дата</th><th scope="col">Запросов</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>"""
    periods = "".join(
        f'<a class="stats-tab{" active" if key == snapshot.period else ""}"'
        f"{' aria-current="page"' if key == snapshot.period else ''}"
        f' rel="nofollow" href="/?period={key}#statistics">{label}</a>'
        for key, label in _PERIOD_LABELS.items()
    )
    generated = snapshot.generated_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Moscow"))
    return f"""<section id="statistics" class="stats">
      <div class="stats-top"><div><div class="stats-kicker">Использование навыка</div><h2>Статистика</h2><div class="stats-muted">Обновлено {generated:%d.%m.%Y %H:%M} МСК</div></div><nav class="stats-tabs" aria-label="Период статистики">{periods}</nav></div>
      <div class="stats-panel"><h3>{_TOTAL_TITLES[snapshot.period]}</h3>{_cards(snapshot.totals)}</div>
      <div class="stats-panel"><h3>{_CHART_TITLES[snapshot.period]}</h3>
        <div class="stats-chart" aria-hidden="true">{bars}</div>
        {table}
      </div>
    </section>"""


# The privacy wording that used to sit under the chart: kept one hover away from
# the word it explains instead of taking a panel of its own.
def _users_hint(form: str) -> str:
    return (
        '<button type="button" class="stats-hint" aria-describedby="users-hint" '
        f'aria-expanded="false">{form}</button>'
        '<span class="stats-tip" id="users-hint" role="tooltip"><strong>Что значит «пользователь»?</strong>'
        "Это стабильный необратимый HMAC-ключ. Исходный Alice ID не сохраняется. "
        "Запросы и сессии в этой статистике тоже представлены только хешами.</span>"
    )


def _ru(value: int) -> str:
    """Group with the non-breaking space Russian uses, and that Intl produces client-side."""
    return f"{value:,}".replace(",", "\u00a0")


def _cards(totals: UsageTotals) -> str:
    values = (
        (
            totals.users,
            f"{plural_form(totals.users, ('активный', 'активных', 'активных'))} "
            f"{_users_hint(plural_form(totals.users, ('пользователь', 'пользователя', 'пользователей')))}",
        ),
        (totals.requests, plural_form(totals.requests, ("запрос", "запроса", "запросов"))),
        (totals.sessions, plural_form(totals.sessions, ("сессия", "сессии", "сессий"))),
        (totals.player_moves, plural_form(totals.player_moves, ("ход игрока", "хода игроков", "ходов игроков"))),
        (totals.games, plural_form(totals.games, ("новая партия", "новые партии", "новых партий"))),
        (
            totals.engaged_games,
            plural_form(totals.engaged_games, ("партия с ходом", "партии с ходом", "партий с ходом")),
        ),
        (
            totals.finished_games,
            plural_form(
                totals.finished_games,
                ("завершённая партия", "завершённые партии", "завершённых партий"),
            ),
        ),
        (
            totals.puzzle_attempts,
            plural_form(
                totals.puzzle_attempts,
                ("шахматная задача", "шахматные задачи", "шахматных задач"),
            ),
        ),
    )
    return (
        '<div class="stats-cards">'
        + "".join(
            # Two layers: the counter animates the decorative one, while the
            # canonical figure stays in the accessibility tree unchanged. Animating
            # the real number meant a screen reader could read "0" mid-count.
            f'<div class="stats-card" style="--delay:{index * 45}ms">'
            f'<div class="stats-value">'
            f'<span class="visually-hidden">{_ru(value)}</span>'
            f'<span class="stats-value-shown" aria-hidden="true" data-count="{value}">{_ru(value)}</span>'
            f"</div>"
            f'<div class="stats-label">{label}</div></div>'
            for index, (value, label) in enumerate(values)
        )
        + "</div>"
    )
