import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from settings_fixtures import TEST_IDENTITY_SALT

from yura_chess.main import create_app
from yura_chess.settings import Settings


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
