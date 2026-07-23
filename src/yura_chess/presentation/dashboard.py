"""Public aggregate usage dashboard with no user-level data."""

# ruff: noqa: E501 - the inline CSS/HTML remains readable as browser-native lines

from __future__ import annotations

from zoneinfo import ZoneInfo

from yura_chess.storage.usage_repository import DashboardSnapshot, UsageTotals

_SOURCE_LABELS = {"real": "Реальные", "test": "Тесты", "all": "Все"}


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    peak = max((point.requests for point in snapshot.daily), default=1) or 1
    bars = "".join(
        f"""<div class="day" title="{point.day:%d.%m}: {point.requests} запросов">
          <div class="bar-value">{point.requests}</div>
          <div class="bar" style="height:{max(4, round(point.requests / peak * 150))}px"></div>
          <time datetime="{point.day.isoformat()}">{point.day:%d.%m}</time>
        </div>"""
        for point in snapshot.daily
    )
    tabs = "".join(
        f'<a class="tab{" active" if key == snapshot.source else ""}" href="/dashboard?source={key}">{label}</a>'
        for key, label in _SOURCE_LABELS.items()
    )
    generated = snapshot.generated_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Moscow"))
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<meta name="robots" content="noindex,nofollow"><title>Статистика — Шахматы с Юрой</title>
<style>
:root{{--bg:#121412;--panel:#1d211d;--panel2:#252b25;--text:#f4f2e8;--muted:#aeb8aa;--green:#9bd36a;--gold:#e8bd66;--line:#343c34}}
*{{box-sizing:border-box}} body{{margin:0;overflow-x:hidden;background:radial-gradient(circle at 15% 0,#27351f 0,transparent 34rem),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,sans-serif}}
main{{width:100%;max-width:1188px;margin:auto;padding:44px 14px 64px}} header{{display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:26px}}
h1{{margin:0;font-size:clamp(30px,5vw,52px);line-height:1.05}} h2{{font-size:19px;margin:0 0 16px}} .eyebrow{{color:var(--green);font-weight:800;letter-spacing:.12em;text-transform:uppercase}}
.muted{{color:var(--muted)}} .tabs{{display:flex;gap:7px;flex-wrap:wrap}} .tab{{color:var(--muted);text-decoration:none;border:1px solid var(--line);padding:8px 13px;border-radius:999px}}
.tab.active{{color:#14200d;background:var(--green);border-color:var(--green);font-weight:800}} .panel{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:22px;padding:22px;margin-bottom:18px}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}} .card{{min-width:0;background:#171a17;border:1px solid var(--line);border-radius:17px;padding:18px}} .value{{font-size:clamp(28px,4vw,43px);font-weight:850;line-height:1;color:var(--gold)}}
.label{{margin-top:8px;color:var(--muted);overflow-wrap:anywhere}} .windows{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:18px}} .chart{{height:205px;display:flex;align-items:end;gap:8px;padding-top:18px;overflow-x:auto}}
.day{{height:180px;min-width:38px;flex:1;display:flex;flex-direction:column;justify-content:end;align-items:center;gap:5px}} .bar{{width:min(30px,80%);background:linear-gradient(#b7ed86,#679b43);border-radius:7px 7px 3px 3px;box-shadow:0 0 24px #75a94b33}}
.bar-value{{font-size:12px;color:var(--muted)}} time{{font-size:11px;color:var(--muted);transform:rotate(-42deg);margin-top:9px}} .note{{display:grid;grid-template-columns:auto 1fr;gap:13px;align-items:start}}
.shield{{font-size:28px}} a{{color:var(--green)}} @media(max-width:850px){{header{{align-items:start;flex-direction:column}}.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.windows{{grid-template-columns:minmax(0,1fr)}}}} @media(max-width:650px){{main{{padding-top:28px}}.cards{{grid-template-columns:minmax(0,1fr)}}.card{{padding:14px}}}}
</style></head><body><main>
<header><div><div class="eyebrow">Шахматы с Юрой</div><h1>Статистика навыка</h1><div class="muted">Обновлено {generated:%d.%m.%Y %H:%M} МСК</div></div><nav class="tabs" aria-label="Тип трафика">{tabs}</nav></header>
<section class="panel"><h2>Последние 24 часа · {_SOURCE_LABELS[snapshot.source].lower()}</h2>{_cards(snapshot.last_24_hours)}</section>
<div class="windows"><section class="panel"><h2>Запросы по дням · 14 дней</h2><div class="chart" role="img" aria-label="Дневное число запросов">{bars}</div></section>
<section class="panel"><h2>За всё время</h2>{_cards(snapshot.all_time)}</section></div>
<section class="panel note"><div class="shield">◈</div><div><strong>Что значит «пользователь»?</strong><br><span class="muted">Это стабильный необратимый HMAC-ключ. Исходный Alice ID не сохраняется. Запросы и сессии в этой статистике также представлены только хешами. Автоматические проверки помечаются как test до хеширования и не входят во вкладку «Реальные».</span></div></section>
</main></body></html>"""


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
        '<div class="cards">'
        + "".join(
            f'<div class="card"><div class="value">{value:,}</div><div class="label">{label}</div></div>'
            for value, label in values
        )
        + "</div>"
    )
