from fastapi.testclient import TestClient

from yura_chess.main import create_app
from yura_chess.settings import Settings


def test_liveness() -> None:
    with TestClient(create_app(Settings(environment="test"))) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "yura-chess",
        "version": "0.1.0",
        "components": None,
    }


def test_readiness() -> None:
    with TestClient(create_app(Settings(environment="test"))) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["components"] == {"http": "ready"}
