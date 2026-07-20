"""Smoke the public production webhook over HTTP when explicitly requested.

    YURA_CHESS_DEPLOYED_URL=https://chess.waxim.ru \
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


async def test_deployed_service_opens_a_game(deployed: httpx.AsyncClient) -> None:
    response = await deployed.post("/alice/webhook", json=throwaway(new=True))

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
    assert "e2e4" in moved["response"]["text"] or "продолж" in moved["response"]["text"].lower()


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
