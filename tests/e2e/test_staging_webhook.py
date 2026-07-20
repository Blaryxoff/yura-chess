"""Smoke the deployed staging webhook over HTTP; skipped unless a URL is given.

Staging is reachable only on Firebat's loopback, so this module is pointed at a
local end of a secure SSH tunnel rather than at a public hostname:

    ssh -N -L 18081:127.0.0.1:8081 firebat
    YURA_CHESS_STAGING_URL=http://127.0.0.1:18081 uv run pytest tests/e2e/test_staging_webhook.py

Unlike the rest of the suite this talks to a real deployment: a real MariaDB, a
real bounded Stockfish pool and real migrations. It therefore uses its own
session and user ids and only ever plays a throwaway game.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from harness import alice_request

# `staging` also opts this module out of the suite's local-database cleanup.
pytestmark = [pytest.mark.anyio, pytest.mark.staging]

STAGING_URL_ENV = "YURA_CHESS_STAGING_URL"
# A staging deployment answers over a tunnel, so the budget is the platform's.
REQUEST_TIMEOUT_SECONDS = 5.0


@pytest.fixture
def staging_url() -> str:
    url = os.environ.get(STAGING_URL_ENV)
    if not url:
        pytest.skip(f"{STAGING_URL_ENV} is not set; this suite needs a deployed staging webhook")
    return url.rstrip("/")


@pytest.fixture
async def staging(staging_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=staging_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        yield client


def throwaway(command: str = "", message_id: int = 1, new: bool = False, **overrides: Any) -> dict[str, Any]:
    """A request under ids that exist only for this run."""
    return alice_request(
        message_id,
        session_id=f"staging-{uuid4()}",
        user_id=f"staging-{uuid4()}",
        command=command,
        new=new,
        **overrides,
    )


async def test_staging_reports_itself_ready(staging: httpx.AsyncClient) -> None:
    """`/health/ready` stays 503 until the connection and the schema check pass."""
    response = await staging.get("/health/ready")

    assert response.status_code == 200


async def test_staging_opens_a_game_over_the_real_stack(staging: httpx.AsyncClient) -> None:
    payload = throwaway(new=True)

    response = await staging.post("/alice/webhook", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "1.0"
    assert body["response"]["text"]
    assert body["user_state_update"]["game_id"]


async def test_staging_plays_a_move_with_the_real_engine_pool(staging: httpx.AsyncClient) -> None:
    session = throwaway(new=True)
    opened = (await staging.post("/alice/webhook", json=session)).json()
    followup = dict(session)
    followup["session"] = dict(session["session"], message_id=2, new=False)
    followup["request"] = {
        "command": "пешка е два е четыре",
        "original_utterance": "пешка е два е четыре",
        "type": "SimpleUtterance",
    }
    followup["state"] = {"user": opened["user_state_update"], "session": opened.get("session_state") or {}}

    moved = (await staging.post("/alice/webhook", json=followup)).json()

    assert moved["response"]["text"]
    assert moved["user_state_update"]["game_id"] == opened["user_state_update"]["game_id"]
    # A real Stockfish answered, or the skill said honestly that it could not.
    assert "e2e4" in moved["response"]["text"] or "продолж" in moved["response"]["text"].lower()


async def test_staging_answers_help_without_touching_a_game(staging: httpx.AsyncClient) -> None:
    session = throwaway(new=True)
    opened = (await staging.post("/alice/webhook", json=session)).json()
    helped = dict(session)
    helped["session"] = dict(session["session"], message_id=2, new=False)
    helped["request"] = {"command": "что ты умеешь", "original_utterance": "что ты умеешь", "type": "SimpleUtterance"}
    helped["state"] = {"user": opened["user_state_update"], "session": opened.get("session_state") or {}}

    answer = (await staging.post("/alice/webhook", json=helped)).json()

    assert "Разделы справки" in answer["response"]["text"]
    # Help reads only: it must not report a new revision for the open game.
    assert answer.get("user_state_update") is None
