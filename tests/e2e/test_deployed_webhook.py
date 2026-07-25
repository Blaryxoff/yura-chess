"""Smoke the public production webhook over HTTP when explicitly requested.

    YURA_CHESS_DEPLOYED_URL=https://yurachess.ru \
      uv run pytest tests/e2e/test_deployed_webhook.py

The tests use throwaway Alice identities and only create disposable games. They
exercise the real MariaDB, migrations and bounded Stockfish pool without a
separate staging environment.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from harness import alice_request

pytestmark = [pytest.mark.anyio, pytest.mark.deployed]

DEPLOYED_URL_ENV = "YURA_CHESS_DEPLOYED_URL"
REQUEST_TIMEOUT_SECONDS = 5.0


@pytest.fixture
def deployed_url() -> str:
    url = os.environ.get(DEPLOYED_URL_ENV)
    if not url:
        pytest.skip(f"{DEPLOYED_URL_ENV} is not set; these tests target the public deployment")
    return url.rstrip("/")


@pytest.fixture
async def deployed(deployed_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=deployed_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        yield client


def throwaway(command: str = "", message_id: int = 1, new: bool = False, **overrides: Any) -> dict[str, Any]:
    suffix = str(uuid4())
    return alice_request(
        message_id,
        session_id=f"deployed-{suffix}",
        user_id=f"deployed-user-{suffix}",
        command=command,
        new=new,
        **overrides,
    )


async def test_deployed_public_entry_is_reachable(deployed: httpx.AsyncClient) -> None:
    response = await deployed.get("/")

    assert response.status_code == 200
    assert "Шахматы с Юрой" in response.text


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("/robots.txt", "Sitemap: https://yurachess.ru/sitemap.xml"),
        ("/sitemap.xml", "<loc>https://yurachess.ru/commands</loc>"),
        ("/how-to-play", "Как играть в шахматы с Алисой голосом"),
        ("/commands", "Голосовые команды шахмат в Алисе"),
        ("/coach", "Шахматный тренер голосом"),
        ("/puzzles", "Шахматные задачи голосом"),
        ("/accessibility", "Шахматы для незрячих голосом"),
        ("/blindfold", "Шахматы вслепую с Алисой"),
        ("/3e123263cd3a154a8aa32da5bc28cebd.txt", "3e123263cd3a154a8aa32da5bc28cebd"),
        ("/yandex_67cb474818f8d2b2.html", "Verification: 67cb474818f8d2b2"),
        ("/favicon.svg", "<svg"),
    ],
)
async def test_deployed_crawlable_surface_is_published(
    deployed: httpx.AsyncClient,
    path: str,
    marker: str,
) -> None:
    """Host nginx allowlists these paths one by one; a stale vhost 404s them while every unit test still passes."""
    response = await deployed.get(path)

    assert response.status_code == 200, f"{path} is not reachable through nginx"
    assert marker in response.text


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/redoc", "/health/ready", "/health/live"])
async def test_deployed_internal_surface_stays_closed(deployed: httpx.AsyncClient, path: str) -> None:
    """The allowlist is only as good as its other half: widening it must not publish an internal route."""
    response = await deployed.get(path)

    assert response.status_code in {403, 404}, f"{path} answered {response.status_code} to the public internet"


@pytest.mark.parametrize("path", ["/webhooks/alice", "/alice/webhook"])
async def test_deployed_both_webhook_paths_answer(deployed: httpx.AsyncClient, path: str) -> None:
    """The console is edited by hand, so the address it points at must never depend on a deploy."""
    response = await deployed.post(path, json=throwaway(new=True))

    assert response.status_code == 200, f"{path} is not reachable through nginx"
    assert response.json()["response"]["text"]


async def test_deployed_service_opens_a_game(deployed: httpx.AsyncClient) -> None:
    response = await deployed.post("/webhooks/alice", json=throwaway(new=True))

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "1.0"
    assert body["response"]["text"]
    assert body["user_state_update"]["game_id"]


async def test_deployed_service_plays_a_move_with_stockfish(deployed: httpx.AsyncClient) -> None:
    session = throwaway(new=True)
    opened = (await deployed.post("/alice/webhook", json=session)).json()
    followup = dict(session)
    followup["session"] = dict(session["session"], message_id=2, new=False)
    followup["request"] = {
        "command": "пешка е два е четыре",
        "original_utterance": "пешка е два е четыре",
        "type": "SimpleUtterance",
    }
    followup["state"] = {"user": opened["user_state_update"], "session": opened.get("session_state") or {}}

    moved = (await deployed.post("/alice/webhook", json=followup)).json()

    assert moved["response"]["text"]
    assert moved["user_state_update"]["game_id"] == opened["user_state_update"]["game_id"]
    assert "e2 e4" in moved["response"]["text"] or "продолж" in moved["response"]["text"].lower()


@pytest.mark.parametrize("command", ["помощь", "что ты умеешь"])
async def test_deployed_service_explains_itself_for_moderation(
    deployed: httpx.AsyncClient,
    command: str,
) -> None:
    response = await deployed.post("/alice/webhook", json=throwaway(command=command, new=True))

    assert response.status_code == 200
    text = response.json()["response"]["text"]
    assert "шахмат" in text.lower()
    assert "новая игра" in text.lower()
    assert "пешка е два е четыре" in text.lower()


@pytest.mark.parametrize("command", ["помощь", "что ты умеешь"])
async def test_deployed_returning_moderator_can_request_help_during_resume_confirmation(
    deployed: httpx.AsyncClient,
    command: str,
) -> None:
    suffix = str(uuid4())
    user_id = f"deployed-moderator-{suffix}"
    await deployed.post(
        "/alice/webhook",
        json=alice_request(1, session_id=f"first-{suffix}", user_id=user_id, new=True),
    )
    prompted = (
        await deployed.post(
            "/alice/webhook",
            json=alice_request(1, session_id=f"return-{suffix}", user_id=user_id, new=True),
        )
    ).json()
    helped = (
        await deployed.post(
            "/alice/webhook",
            json=alice_request(
                2,
                session_id=f"return-{suffix}",
                user_id=user_id,
                command=command,
                session_state=prompted["session_state"],
            ),
        )
    ).json()

    assert "шахмат" in prompted["response"]["text"].lower()
    assert "помощь" in prompted["response"]["text"].lower()
    assert "пешка е два е четыре" in helped["response"]["text"].lower()
    assert "да» или «нет" not in helped["response"]["text"].lower()
