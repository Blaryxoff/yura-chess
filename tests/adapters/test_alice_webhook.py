"""Golden tests for the Alice webhook: identity, ownership, replay and deadline."""

from __future__ import annotations

import asyncio
from typing import Any

import chess
import httpx
import pytest
from pydantic import SecretStr
from settings_fixtures import TEST_IDENTITY_SALT, UNREACHABLE_DATABASE_URL
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.adapters.alice.models import STATE_LIMIT_BYTES, TEXT_LIMIT, AliceRequest
from yura_chess.application.player_identity import UnidentifiedRequestError, owner_key
from yura_chess.domain.game import PlayerColor
from yura_chess.main import create_app
from yura_chess.settings import Settings
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository

pytestmark = pytest.mark.anyio

SKILL = "skill-under-test"
USER_A = "alice-user-a"
USER_B = "alice-user-b"


class FakeEngine:
    """Answers with the first legal move; never a real Stockfish process."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.searches = 0

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        self.searches += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return next(iter(board.legal_moves)).uci()


def build_client(
    session_factory: sessionmaker[Session],
    engine: FakeEngine | None = None,
    deadline: float = 4.5,
) -> httpx.AsyncClient:
    """Wire the app by hand: the lifespan would open its own database engine."""
    settings = Settings(  # type: ignore[call-arg]
        environment="test",
        database_url=UNREACHABLE_DATABASE_URL,
        identity_salt=TEST_IDENTITY_SALT,
        webhook_deadline_seconds=deadline,
    )
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.engine_pool = engine or FakeEngine()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def alice_request(
    message_id: int,
    session_id: str = "session-1",
    user_id: str | None = USER_A,
    command: str = "",
    new: bool = False,
    state: dict[str, Any] | None = None,
    session_state: dict[str, Any] | None = None,
    screen: bool = False,
) -> dict[str, Any]:
    session: dict[str, Any] = {
        "message_id": message_id,
        "session_id": session_id,
        "skill_id": SKILL,
        "new": new,
        "application": {"application_id": "device-1"},
    }
    if user_id is not None:
        session["user"] = {"user_id": user_id}
    return {
        "meta": {"locale": "ru-RU", "interfaces": {"screen": {}} if screen else {}},
        "session": session,
        "request": {"command": command, "original_utterance": command, "type": "SimpleUtterance"},
        "state": {"user": state or {}, "session": session_state or {}},
        "version": "1.0",
    }


def games_count(database_engine: Engine) -> int:
    with database_engine.begin() as connection:
        return int(connection.execute(text("SELECT COUNT(*) FROM games")).scalar_one())


async def test_a_new_session_opens_a_game_and_returns_minimal_state(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        response = await client.post("/alice/webhook", json=alice_request(1, new=True))

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "1.0"
    assert set(body["user_state_update"]) == {"game_id", "revision"}
    assert len(body["response"]["text"]) <= TEXT_LIMIT
    assert len(str(body["user_state_update"]).encode("utf-8")) <= STATE_LIMIT_BYTES


async def test_a_sequence_of_requests_stays_on_the_same_game(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        first = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        state = first["user_state_update"]
        second = (
            await client.post(
                "/alice/webhook",
                json=alice_request(2, state=state, session_state=first.get("session_state")),
            )
        ).json()

    assert second["user_state_update"]["game_id"] == state["game_id"]
    assert games_count(database_engine) == 1


async def test_a_spoken_move_uses_the_real_router_and_response_composer(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        first = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        moved = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="пешка е два е четыре",
                    state=first["user_state_update"],
                    session_state=first.get("session_state"),
                ),
            )
        ).json()

    assert moved["user_state_update"]["revision"] > first["user_state_update"]["revision"]
    assert "Ваш ход: e2e4" in moved["response"]["text"]
    assert "Мой ход" in moved["response"]["text"]
    assert moved["session_state"]["last_heard"] == "пешка е два е четыре"


async def test_destructive_confirmation_survives_alice_session_state(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        first = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        asked = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="сдаюсь",
                    state=first["user_state_update"],
                    session_state=first.get("session_state"),
                ),
            )
        ).json()
        confirmed = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    3,
                    command="да",
                    state=first["user_state_update"],
                    session_state=asked["session_state"],
                ),
            )
        ).json()

    assert asked["session_state"]["pending_action"]["kind"] == "resign"
    assert "действительно" in asked["response"]["text"]
    assert "Партия окончена" in confirmed["response"]["text"]


async def test_slow_repeat_survives_alice_session_state(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        first = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        repeated = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="повтори медленно",
                    state=first["user_state_update"],
                    session_state=first["session_state"],
                ),
            )
        ).json()

    assert first["session_state"]["last_reply"]
    assert repeated["response"]["text"].startswith("Повторяю:")
    assert repeated["response"]["tts"].startswith("Повторяю медленно.")


async def test_a_foreign_game_id_reveals_nothing_and_never_touches_that_game(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        owned = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        foreign = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    session_id="session-b",
                    user_id=USER_B,
                    state=owned["user_state_update"],
                ),
            )
        ).json()

    assert foreign["user_state_update"]["game_id"] != owned["user_state_update"]["game_id"]
    # The intruder gets a fresh game of their own, and A's game is untouched.
    assert foreign["user_state_update"]["revision"] == owned["user_state_update"]["revision"]


async def test_a_corrupted_state_falls_back_to_a_new_game(session_factory: sessionmaker[Session]) -> None:
    async with build_client(session_factory) as client:
        response = await client.post(
            "/alice/webhook",
            json=alice_request(1, state={"game_id": "not-a-real-game", "revision": 99}),
        )

    assert response.status_code == 200
    assert response.json()["user_state_update"]["game_id"] != "not-a-real-game"


async def test_an_exact_redelivery_replays_the_stored_answer(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    payload = alice_request(1, new=True)
    async with build_client(session_factory) as client:
        first = (await client.post("/alice/webhook", json=payload)).json()
        second = (await client.post("/alice/webhook", json=payload)).json()

    assert first == second
    assert games_count(database_engine) == 1


async def test_a_reused_replay_key_with_another_command_is_rejected_without_changing_the_game(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        first = (await client.post("/alice/webhook", json=alice_request(1, new=True, command="играем"))).json()
        conflict = await client.post("/alice/webhook", json=alice_request(1, new=True, command="сдаюсь"))

    assert conflict.status_code == 200
    assert conflict.json().get("user_state_update") is None
    assert games_count(database_engine) == 1
    assert first["user_state_update"]["revision"] == 1


async def test_parallel_requests_for_one_game_leave_it_consistent(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        state = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()["user_state_update"]
        responses = await asyncio.gather(
            client.post("/alice/webhook", json=alice_request(2, state=state)),
            client.post("/alice/webhook", json=alice_request(3, state=state)),
        )

    assert [response.status_code for response in responses] == [200, 200]
    ids = {response.json()["user_state_update"]["game_id"] for response in responses}
    assert ids == {state["game_id"]}
    assert games_count(database_engine) == 1


async def test_independent_users_get_independent_games(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        a = (await client.post("/alice/webhook", json=alice_request(1, new=True, user_id=USER_A))).json()
        b = (
            await client.post(
                "/alice/webhook",
                json=alice_request(1, session_id="session-b", new=True, user_id=USER_B),
            )
        ).json()

    assert a["user_state_update"]["game_id"] != b["user_state_update"]["game_id"]
    assert games_count(database_engine) == 2


async def test_an_unidentified_request_gets_no_game_and_no_state(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    payload = alice_request(1, new=True, user_id=None)
    del payload["session"]["application"]

    async with build_client(session_factory) as client:
        response = await client.post("/alice/webhook", json=payload)

    assert response.status_code == 200
    assert response.json().get("user_state_update") is None
    assert games_count(database_engine) == 0


async def test_an_invalid_protocol_payload_is_rejected(session_factory: sessionmaker[Session]) -> None:
    async with build_client(session_factory) as client:
        response = await client.post("/alice/webhook", json={"version": "1.0"})

    assert response.status_code == 422


async def test_a_slow_turn_answers_within_the_deadline(session_factory: sessionmaker[Session]) -> None:
    owner = owner_key(SecretStr(TEST_IDENTITY_SALT), USER_A, None)
    with session_scope(session_factory) as session:
        repository = GameRepository(session)
        state = repository.create_game(owner, PlayerColor.WHITE)
        # After the player's move the engine owes a reply, so the search runs.
        state = repository.append_moves(state.id, owner, state.revision, ("e2e4",))
        game_id = state.id

    engine = FakeEngine(delay=1.0)
    async with build_client(session_factory, engine, deadline=0.2) as client:
        response = await client.post(
            "/alice/webhook",
            json=alice_request(1, state={"game_id": game_id, "revision": state.revision}),
        )

    assert response.status_code == 200
    # No state is promised for an answer the skill could not finish.
    assert response.json().get("user_state_update") is None


def test_screen_support_is_read_from_the_interfaces() -> None:
    assert AliceRequest.model_validate(alice_request(1, screen=True)).has_screen is True
    assert AliceRequest.model_validate(alice_request(1)).has_screen is False


def test_the_owner_key_is_pseudonymous_and_scoped() -> None:
    salt = SecretStr("salt")
    key = owner_key(salt, USER_A, "device-1")

    assert len(key) == 64
    assert USER_A not in key
    # The account wins over the device, and the two namespaces never collide.
    assert key != owner_key(salt, None, "device-1")
    assert key != owner_key(SecretStr("other"), USER_A, None)
    assert owner_key(salt, None, USER_A) != key

    with pytest.raises(UnidentifiedRequestError):
        owner_key(salt, None, None)
