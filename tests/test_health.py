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
    FAVICON_SVG,
    ROBOTS_PATH,
    ROBOTS_TEXT,
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
    assert "Всё, что умеет навык" in response.text
    assert "Решайте шахматные задачи" in response.text
    assert "Задача на мат в два" in response.text
    assert 'class="command-list"' in response.text
    assert response.text.index('class="support-action hero-support"') < response.text.index("Всё, что умеет навык")
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
    assert "prefers-reduced-motion: reduce" in response.text
    assert "Как играть в голосовые шахматы с Алисой" in response.text
    structured_data = response.text.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    graph = json.loads(structured_data)["@graph"]
    assert {item["@type"] for item in graph} == {"WebSite", "SoftwareApplication", "FAQPage"}
    assert "незряч" not in response.text.lower()


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
