import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from settings_fixtures import TEST_IDENTITY_SALT

from yura_chess.main import _purge_retained_data, create_app
from yura_chess.presentation.website import (
    ACCESSIBILITY_PATH,
    BLINDFOLD_PATH,
    COACH_PATH,
    COMMANDS_PATH,
    FAVICON_SVG,
    HOW_TO_PLAY_PATH,
    INDEXNOW_KEY,
    INDEXNOW_KEY_PATH,
    LANDING_FAQ,
    LANDING_PATH,
    PUZZLES_PATH,
    ROBOTS_PATH,
    ROBOTS_TEXT,
    SITEMAP_ENTRIES,
    SITEMAP_PATH,
    SITEMAP_XML,
    WEBMASTER_VERIFICATION_HTML,
    WEBMASTER_VERIFICATION_PATH,
)
from yura_chess.settings import Settings
from yura_chess.storage.usage_repository import DailyUsage, DashboardSnapshot, UsageTotals


def test_liveness_does_not_depend_on_the_database(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "yura-chess",
        "version": "0.1.0",
        "components": None,
    }


def test_public_landing_page_describes_the_skill_for_everyone(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    totals = UsageTotals(2, 1, 1, 1, 1, 1, 0, 0)
    snapshot = DashboardSnapshot(
        "real",
        "month",
        datetime(2026, 7, 23, 12, 0, 0),
        totals,
        (DailyUsage(date(2026, 7, 23), requests=2),),
    )
    monkeypatch.setattr(
        "yura_chess.main.UsageRepository.dashboard",
        lambda self, source, *, period: snapshot,
    )
    with TestClient(create_app(offline_settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=300"
    assert "Шахматы с Юрой" in response.text
    assert "Stockfish" in response.text
    assert "с&nbsp;естественными командами" in response.text
    assert "Включи режим тренера" in response.text
    assert "Настоящие шахматы в Алисе" in response.text
    assert "Продолжайте позже" in response.text
    assert "Играйте без экрана" in response.text
    assert "Как играть в шахматы с Алисой?" in response.text
    assert "Нужен ли экран, чтобы играть?" in response.text
    assert "Есть ли режим тренера и разбор партии?" in response.text
    assert "Как узнать все команды?" in response.text
    assert "Задача на мат в два хода" in response.text
    assert 'class="command-list"' in response.text
    assert response.text.index('class="support-action hero-support"') < response.text.index("Настоящие шахматы в Алисе")
    assert response.text.index('id="statistics"') < response.text.index("Конфиденциальность")
    assert response.text.index('id="statistics"') < response.text.index('id="support"')
    assert response.text.index('id="support"') < response.text.index("Конфиденциальность")
    assert 'href="https://pay.cloudtips.ru/p/f604e20f"' in response.text
    assert response.text.count('href="https://pay.cloudtips.ru/p/f604e20f"') == 2
    assert 'rel="noopener noreferrer nofollow"' in response.text
    assert "Поддержка не предоставляет платных функций" in response.text
    assert '<link rel="icon" href="/favicon.svg"' in response.text
    assert '<link rel="canonical" href="https://chess.waxim.ru/">' in response.text
    assert '<meta property="og:type" content="website">' in response.text
    assert '<script type="application/ld+json">' in response.text
    assert "IntersectionObserver" in response.text
    assert "chart.scrollWidth - chart.clientWidth" in response.text
    assert "current.replaceWith(replacement)" in response.text
    assert 'history.pushState({ statistics: true }, "", url)' in response.text
    assert "window.scrollTo({ top: scrollPosition })" in response.text
    assert 'window.addEventListener("popstate"' in response.text
    assert "prefers-reduced-motion: reduce" in response.text
    assert "Как играть в голосовые шахматы с Алисой" in response.text
    structured_data = response.text.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    graph = json.loads(structured_data)["@graph"]
    assert {item["@type"] for item in graph} == {"WebSite", "SoftwareApplication", "FAQPage"}
    faq = next(item for item in graph if item["@type"] == "FAQPage")
    assert [item["name"] for item in faq["mainEntity"]] == [question for question, _ in LANDING_FAQ]
    # A FAQ rich result is dropped when the marked-up answer is not on the page.
    for question, answer in LANDING_FAQ:
        assert question in response.text
        assert answer in response.text
    assert "незряч" in response.text.lower()
    for path in (HOW_TO_PLAY_PATH, COMMANDS_PATH, COACH_PATH, PUZZLES_PATH, ACCESSIBILITY_PATH, BLINDFOLD_PATH):
        assert f'href="{path}"' in response.text
    # The privacy wording moved into a hover tooltip on the word it explains.
    assert "Что значит «пользователь»?" in response.text
    assert 'class="stats-hint"' in response.text
    assert 'aria-describedby="users-hint"' in response.text
    assert "Автоматические проверки" not in response.text
    # The card, not the inline word, positions the tooltip: an inline anchor both
    # mispositions the panel and traps its z-index inside a sibling stacking context.
    assert ".stats-card { position: relative;" in response.text
    assert ".stats-card:has(.stats-hint:hover) { z-index: 4; }" in response.text
    # Default placement is under the card so the counter it explains stays readable.
    assert "top: calc(100% + 12px);" in response.text
    assert ".stats-card.tip-above .stats-tip {" in response.text
    assert 'card.classList.add("tip-above")' in response.text


def test_yandex_webmaster_verification_file_is_served_verbatim(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(WEBMASTER_VERIFICATION_PATH)

    assert response.status_code == 200
    assert response.text == WEBMASTER_VERIFICATION_HTML


def test_search_engine_discovery_files_are_public_and_cacheable(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        robots = client.get(ROBOTS_PATH)
        sitemap = client.get(SITEMAP_PATH)

    assert robots.status_code == sitemap.status_code == 200
    assert robots.text == ROBOTS_TEXT
    assert "text/plain" in robots.headers["content-type"]
    assert "Clean-param: source&period /" in robots.text
    assert sitemap.text == SITEMAP_XML
    assert "application/xml" in sitemap.headers["content-type"]
    assert "https://chess.waxim.ru/" in sitemap.text
    for path, _ in SITEMAP_ENTRIES:
        assert f"<loc>https://chess.waxim.ru{path}</loc>" in sitemap.text


def test_indexnow_key_is_served_so_submissions_are_accepted(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(INDEXNOW_KEY_PATH)

    assert response.status_code == 200
    assert response.text == INDEXNOW_KEY
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.parametrize(
    ("path", "title", "marker"),
    [
        (HOW_TO_PLAY_PATH, "Как играть в шахматы с Алисой голосом", "Уровень Stockfish — от нуля"),
        (COMMANDS_PATH, "Голосовые команды шахмат в Алисе", "«повтори координаты по буквам»"),
        (COACH_PATH, "Шахматный тренер голосом", "Подсказки по ступеням"),
        (PUZZLES_PATH, "Шахматные задачи голосом", "Мат по последней горизонтали"),
        (ACCESSIBILITY_PATH, "Шахматы для незрячих голосом", "Тренер и задачи тоже без экрана"),
        (BLINDFOLD_PATH, "Шахматы вслепую с Алисой", "Как наращивать сложность"),
    ],
)
def test_secondary_pages_are_crawlable_and_self_describing(
    offline_settings: Settings,
    path: str,
    title: str,
    marker: str,
) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert title in response.text
    assert marker in response.text
    assert f'<link rel="canonical" href="https://chess.waxim.ru{path}">' in response.text
    # Every secondary page must lead back to the others, or a crawler reaches none of them.
    every_page = {
        LANDING_PATH,
        HOW_TO_PLAY_PATH,
        COMMANDS_PATH,
        COACH_PATH,
        PUZZLES_PATH,
        ACCESSIBILITY_PATH,
        BLINDFOLD_PATH,
    }
    for linked in every_page - {path}:
        assert f'href="{linked}"' in response.text
    # Getting home must not depend on the browser's back button.
    assert 'class="piece home" href="/"' in response.text
    assert response.text.index('class="breadcrumbs"') < response.text.index("<h1>")
    assert f'<a href="{path}" aria-current="page">' in response.text
    structured_data = response.text.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    graph = json.loads(structured_data)["@graph"]
    assert "BreadcrumbList" in {item["@type"] for item in graph}
    # The statistics dashboard belongs to the landing page only.
    assert 'id="statistics"' not in response.text


def test_favicon_is_served_for_modern_and_legacy_browser_paths(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        svg = client.get("/favicon.svg")
        ico = client.get("/favicon.ico")
        head = client.head("/favicon.svg")

    assert svg.status_code == ico.status_code == head.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["cache-control"] == "public, max-age=86400"
    assert svg.text == ico.text == FAVICON_SVG


def test_public_landing_page_uses_real_traffic_and_accepts_period_filters(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    queries: list[tuple[str, str]] = []
    totals = UsageTotals(2, 1, 1, 1, 1, 1, 0, 0)
    snapshot = DashboardSnapshot(
        "real",
        "month",
        datetime(2026, 7, 23, 12, 0, 0),
        totals,
        (DailyUsage(date(2026, 7, 23), requests=2),),
    )

    @contextmanager
    def fake_session_scope(session_factory: object) -> Iterator[object]:
        yield object()

    class Repository:
        def __init__(self, session: object) -> None:
            return None

        def dashboard(self, source: str, *, period: str) -> DashboardSnapshot:
            queries.append((source, period))
            return snapshot

    monkeypatch.setattr("yura_chess.main.session_scope", fake_session_scope)
    monkeypatch.setattr("yura_chess.main.UsageRepository", Repository)
    with TestClient(create_app(offline_settings)) as client:
        default = client.get("/")
        test = client.get("/?source=test&period=year")
        head = client.head("/")
        invalid = client.get("/?source=private")
        invalid_period = client.get("/?period=week")
        removed_dashboard = client.get("/dashboard")

    assert default.status_code == test.status_code == head.status_code == 200
    assert default.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=300"
    assert queries == [("real", "month"), ("real", "year"), ("real", "month"), ("real", "month")]
    assert '<link rel="canonical" href="https://chess.waxim.ru/">' in test.text
    assert invalid.status_code == 200
    assert invalid_period.status_code == 422
    assert removed_dashboard.status_code == 404


def test_readiness_reports_an_unreachable_database(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["http"] == "ready"
    assert body["components"]["database"].startswith("unavailable")
    assert body["components"]["engine"].endswith("workers")


def test_readiness_counts_ready_engine_workers_without_searching(offline_settings: Settings) -> None:
    searches = 0

    class NeverSearchedProcess:
        def best_move(self, board: object, search_time: float) -> str:
            nonlocal searches
            searches += 1
            return "e2e4"

        def close(self) -> None:
            return None

    app = create_app(offline_settings)
    app.state.engine_process_factory = NeverSearchedProcess
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.json()["components"]["engine"] == "ready: 2/2 workers"
    assert searches == 0


def test_periodic_maintenance_includes_remote_board_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    @contextmanager
    def fake_session_scope(session_factory: object) -> Iterator[object]:
        yield object()

    class TranscriptRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_expired(self, now: object, retention_days: int) -> None:
            calls.append("transcripts")

    class GameRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_request_replays(self, now: object, retention_days: int) -> None:
            calls.append("replays")

    class AnalysisRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_expired(self, now: object, retention_days: int) -> None:
            calls.append("analysis")

    class ReviewRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_expired(self, now: object, retention_days: int) -> None:
            calls.append("reviews")

    monkeypatch.setattr("yura_chess.main.session_scope", fake_session_scope)
    monkeypatch.setattr("yura_chess.main.TranscriptRepository", TranscriptRepository)
    monkeypatch.setattr("yura_chess.main.GameRepository", GameRepository)
    monkeypatch.setattr("yura_chess.main.AnalysisRepository", AnalysisRepository)
    monkeypatch.setattr("yura_chess.main.ReviewRepository", ReviewRepository)
    app = SimpleNamespace(
        state=SimpleNamespace(
            session_factory=object(),
            settings=SimpleNamespace(
                asr_transcript_retention_days=30,
                request_replay_retention_days=7,
                analysis_checkpoint_retention_days=180,
                review_state_retention_days=30,
            ),
            board_images=SimpleNamespace(maintain_cache=lambda: calls.append("images")),
        )
    )

    _purge_retained_data(app)

    assert calls == ["transcripts", "replays", "analysis", "reviews", "images"]


def test_a_missing_stockfish_binary_does_not_block_startup(offline_settings: Settings) -> None:
    app = create_app(offline_settings.model_copy(update={"stockfish_path": Path("/nonexistent/stockfish")}))
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.json()["components"]["engine"] == "degraded: 0/2 workers"


class _StubProcess:
    """Stands in for a live Stockfish so readiness does not need a host binary."""

    def best_move(self, board: object, search_time: float) -> str:
        return "e2e4"

    def close(self) -> None:
        return None


def _migrated_settings() -> Settings:
    dsn = os.environ.get("YURA_CHESS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("YURA_CHESS_TEST_DATABASE_URL is not set; readiness needs a migrated MariaDB")
    return Settings(environment="test", database_url=dsn, identity_salt=TEST_IDENTITY_SALT)  # type: ignore[arg-type]


def test_readiness_is_green_against_a_migrated_database() -> None:
    app = create_app(_migrated_settings())
    app.state.engine_process_factory = _StubProcess
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    components = response.json()["components"]
    assert components["http"] == "ready"
    assert components["database"] == "ready"
    assert components["engine"].startswith("ready")


def test_readiness_fails_when_the_database_is_up_but_no_engine_worker_is() -> None:
    settings = _migrated_settings().model_copy(update={"stockfish_path": Path("/nonexistent/stockfish")})
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    components = response.json()["components"]
    assert components["database"] == "ready"
    assert components["engine"] == "degraded: 0/2 workers"
