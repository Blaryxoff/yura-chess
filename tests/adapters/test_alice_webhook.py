"""Golden tests for the Alice webhook: identity, ownership, replay and deadline."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import chess
import httpx
import pytest
from pydantic import SecretStr
from settings_fixtures import TEST_IDENTITY_SALT, UNREACHABLE_DATABASE_URL
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.adapters.alice.models import (
    CARD_ITEMS_LIMIT,
    STATE_LIMIT_BYTES,
    TEXT_LIMIT,
    TTS_LIMIT,
    AliceRequest,
)
from yura_chess.adapters.alice.webhook import _conversation_state, _session_state_update
from yura_chess.application.command_router import CommandKind, PendingClarification
from yura_chess.application.conversation import ConversationState, PendingAction
from yura_chess.application.player_identity import UnidentifiedRequestError, owner_key
from yura_chess.domain.game import PlayerColor
from yura_chess.main import create_app
from yura_chess.presentation.help_speech import HelpState, HelpTopic
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


async def test_a_new_session_recovers_and_confirms_the_latest_unfinished_game(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        moved = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="пешка е два е четыре",
                    state=opened["user_state_update"],
                    session_state=opened.get("session_state"),
                ),
            )
        ).json()
        prompted = (
            await client.post(
                "/alice/webhook",
                json=alice_request(1, session_id="session-2", new=True),
            )
        ).json()
        resumed = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    session_id="session-2",
                    command="да",
                    session_state=prompted["session_state"],
                ),
            )
        ).json()

    assert "Продолжить?" in prompted["response"]["text"]
    assert "Последние два хода" in prompted["response"]["text"]
    assert prompted["session_state"]["pending_action"]["kind"] == "continue"
    assert prompted["session_state"]["game_id"] == moved["user_state_update"]["game_id"]
    assert resumed["user_state_update"]["game_id"] == moved["user_state_update"]["game_id"]
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


async def test_the_help_menu_and_the_full_catalogue_survive_alice_session_state(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        menu = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="справка",
                    state=opened["user_state_update"],
                    session_state=opened.get("session_state"),
                ),
            )
        ).json()
        catalogue = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    3,
                    command="дальше",
                    state=opened["user_state_update"],
                    session_state=menu["session_state"],
                ),
            )
        ).json()
        second_page = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    4,
                    command="дальше",
                    state=opened["user_state_update"],
                    session_state=catalogue["session_state"],
                ),
            )
        ).json()

    # The open menu is state with no topic yet, which is what makes «дальше» read
    # the whole catalogue instead of the board.
    assert menu["session_state"]["help"] == {"page": 0}
    assert "Разделы справки" in menu["response"]["text"]
    assert catalogue["session_state"]["help"] == {"topic": "all", "page": 0}
    assert second_page["session_state"]["help"] == {"topic": "all", "page": 1}
    # Reading help changes nothing about the game: no durable state update at all.
    assert "user_state_update" not in second_page
    assert second_page["session_state"]["revision"] == opened["user_state_update"]["revision"]
    assert games_count(database_engine) == 1


async def test_a_help_topic_is_paged_and_then_cleared_by_the_next_command(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        topic = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="справка про партию",
                    state=opened["user_state_update"],
                    session_state=opened.get("session_state"),
                ),
            )
        ).json()
        paged = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    3,
                    command="дальше",
                    state=opened["user_state_update"],
                    session_state=topic["session_state"],
                ),
            )
        ).json()
        closed = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    4,
                    command="выйти из справки",
                    state=opened["user_state_update"],
                    session_state=paged["session_state"],
                ),
            )
        ).json()
        after = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    5,
                    command="дальше",
                    state=opened["user_state_update"],
                    session_state=closed["session_state"],
                ),
            )
        ).json()

    assert topic["session_state"]["help"] == {"topic": "game", "page": 0}
    assert paged["session_state"]["help"] == {"topic": "game", "page": 1}
    assert "help" not in closed["session_state"]
    # With help closed «дальше» is board pagination again, not help navigation.
    assert "help" not in after["session_state"]
    assert "Раздел" not in after["response"]["text"]


async def test_a_corrupted_help_state_is_ignored_and_an_out_of_range_page_is_clamped(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        session_state = dict(opened.get("session_state") or {})
        session_state["help"] = {"topic": "no-such-topic", "page": 999}
        answered = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="дальше",
                    state=opened["user_state_update"],
                    session_state=session_state,
                ),
            )
        ).json()

        session_state["help"] = {"topic": "game", "page": 999}
        clamped = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    3,
                    command="дальше",
                    state=opened["user_state_update"],
                    session_state=session_state,
                ),
            )
        ).json()

    assert answered.get("session_state", {}).get("help") is None
    assert "Раздел" not in answered["response"]["text"]
    # A page past the end of a real topic is pulled back to the last one.
    assert clamped["session_state"]["help"] == {"topic": "game", "page": 1}


async def test_a_help_answer_stays_inside_the_platform_limits_without_a_screen(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        catalogue = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    2,
                    command="все команды",
                    state=opened["user_state_update"],
                    session_state=opened.get("session_state"),
                ),
            )
        ).json()

    body = catalogue["response"]
    assert len(body["text"]) <= TEXT_LIMIT
    assert len(body.get("tts") or "") <= TTS_LIMIT
    assert len(json.dumps(catalogue["session_state"], ensure_ascii=False).encode("utf-8")) <= STATE_LIMIT_BYTES
    # Nothing the player needs may live only on a screen.
    assert body.get("card") is None
    assert "Раздел «ходы»" in body["text"]


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
    engine = FakeEngine()
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
    payload = alice_request(
        2,
        command="пешка е два е четыре",
        state=opened["user_state_update"],
        session_state=opened.get("session_state"),
    )
    async with build_client(session_factory, engine) as client:
        first = (await client.post("/alice/webhook", json=payload)).json()
        second = (await client.post("/alice/webhook", json=payload)).json()

    assert first == second
    assert games_count(database_engine) == 1
    assert engine.searches == 1


async def test_a_reused_replay_key_with_another_command_is_rejected_without_changing_the_game(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    engine = FakeEngine()
    async with build_client(session_factory, engine) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        original = alice_request(
            2,
            command="пешка е два е четыре",
            state=opened["user_state_update"],
            session_state=opened.get("session_state"),
        )
        await client.post("/alice/webhook", json=original)
        conflict_payload = alice_request(
            2,
            command="сдаюсь",
            state=opened["user_state_update"],
            session_state=opened.get("session_state"),
        )
        conflict = await client.post("/alice/webhook", json=conflict_payload)

    assert conflict.status_code == 200
    assert conflict.json()["response"]["text"] == "Не расслышала. Повторите, пожалуйста."
    assert conflict.json().get("user_state_update") is None
    assert games_count(database_engine) == 1
    assert engine.searches == 1


async def test_a_conversation_only_answer_replays_after_the_board_changes(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
        query = alice_request(
            2,
            command="что на эф три",
            state=opened["user_state_update"],
            session_state=opened.get("session_state"),
        )
        first = (await client.post("/alice/webhook", json=query)).json()
        moved = (
            await client.post(
                "/alice/webhook",
                json=alice_request(
                    3,
                    command="пешка е два е четыре",
                    state=opened["user_state_update"],
                    session_state=first.get("session_state"),
                ),
            )
        ).json()
        replayed = (await client.post("/alice/webhook", json=query)).json()

    assert moved["user_state_update"]["revision"] > opened["user_state_update"]["revision"]
    assert replayed == first


async def test_a_timed_out_move_retries_before_the_router_can_reinterpret_it(
    session_factory: sessionmaker[Session],
) -> None:
    async with build_client(session_factory) as client:
        opened = (await client.post("/alice/webhook", json=alice_request(1, new=True))).json()
    move = alice_request(
        2,
        command="пешка е два е четыре",
        state=opened["user_state_update"],
        session_state=opened.get("session_state"),
    )

    async with build_client(session_factory, FakeEngine(delay=1.0), deadline=0.05) as client:
        timed_out = (await client.post("/alice/webhook", json=move)).json()
    fast_engine = FakeEngine()
    async with build_client(session_factory, fast_engine) as client:
        resumed = (await client.post("/alice/webhook", json=move)).json()

    assert timed_out.get("user_state_update") is None
    assert "Ваш ход: e2e4" in resumed["response"]["text"]
    assert resumed["user_state_update"]["revision"] == 3
    assert fast_engine.searches == 1


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


async def test_a_help_card_is_optional_and_the_voice_answer_is_unchanged(
    session_factory: sessionmaker[Session],
) -> None:
    """Screen and no-screen answers say the same thing; only the card differs."""
    async with build_client(session_factory) as client:
        spoken = (await client.post("/alice/webhook", json=alice_request(1, command="справка"))).json()
        shown = (
            await client.post(
                "/alice/webhook",
                json=alice_request(1, session_id="session-2", command="справка", screen=True),
            )
        ).json()

    assert spoken["response"].get("card") is None
    assert shown["response"]["text"] == spoken["response"]["text"]
    card = shown["response"]["card"]
    assert card["type"] == "ItemsList"
    assert card["header"]["text"] == "Справка"
    assert 1 <= len(card["items"]) <= CARD_ITEMS_LIMIT
    # Every listed topic was already offered by the spoken menu.
    assert all(item["description"] for item in card["items"])


def test_the_review_page_flag_survives_the_alice_session_state() -> None:
    """«дальше» keeps turning the review's pages after a round trip through Alice."""
    sent = _session_state_update(ConversationState(game_id="game-1", revision=2, reviewing=True))
    payload = alice_request(2, session_state=sent.model_dump(exclude_none=True))

    restored = _conversation_state(AliceRequest.model_validate(payload))

    assert sent.reviewing is True
    assert restored.reviewing is True
    assert _conversation_state(AliceRequest.model_validate(alice_request(3))).reviewing is False


def test_no_durable_identifier_other_than_the_game_reaches_the_client() -> None:
    """The review and the puzzle attempt stay server-side; only the game is claimed."""
    sent = _session_state_update(ConversationState(game_id="game-1", revision=2, reviewing=True, position_page=1))

    assert set(sent.model_dump(exclude_none=True)) <= {
        "game_id",
        "revision",
        "last_heard",
        "last_reply",
        "clarification",
        "pending_action",
        "position_page",
        "help",
        "reviewing",
    }


def test_the_session_state_is_kept_inside_the_platform_limit() -> None:
    """A worst-case dialog drops what can be repeated, never the game it is about."""
    state = ConversationState(
        game_id="1" * 36,
        revision=99,
        last_heard="я" * 255,
        last_reply="я" * 512,
        clarification=PendingClarification("я" * 255, tuple(f"кандидат-{index}" for index in range(16))),
        pending_action=PendingAction(CommandKind.NEW_GAME, "я" * 255),
        position_page=3,
        help=HelpState(topic=HelpTopic.ALL, page=2),
        reviewing=True,
    )

    trimmed = _session_state_update(state)

    assert len(trimmed.model_dump_json(exclude_none=True).encode("utf-8")) <= STATE_LIMIT_BYTES
    assert trimmed.game_id == state.game_id
    assert trimmed.revision == state.revision
    assert trimmed.reviewing is True
    # What was dropped is exactly what the next request can be told again.
    assert trimmed.last_reply is None
