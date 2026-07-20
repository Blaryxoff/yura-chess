"""Drivers shared by the end-to-end modules: one fake engine and one Alice client.

Everything here is deliberately deterministic. The suite proves that the whole
skill holds together across transports and modes, so the only thing it must not
depend on is a real Stockfish process or a real Yandex account.
"""

from __future__ import annotations

import asyncio
from typing import Any

import chess
import httpx
from settings_fixtures import TEST_IDENTITY_SALT, UNREACHABLE_DATABASE_URL
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.game_service import RequestContext
from yura_chess.domain.analysis import MoveCandidate, PositionAnalysis, Score
from yura_chess.domain.game import PlayerColor
from yura_chess.engine.stockfish import EngineSearchTimeoutError, EngineUnavailableError
from yura_chess.main import create_app
from yura_chess.settings import Settings

SKILL = "e2e-skill"
USER_A = "e2e-user-a"
USER_B = "e2e-user-b"


class FakeEngine:
    """Answers moves and analysis without a process, and fails exactly on demand.

    `move_failures` makes the first N searches unavailable, which is how a real
    pool leaves a pending engine turn behind; `analysis_timeout` makes every
    read-only analysis time out, which every training and review path must
    survive without changing the game.
    """

    def __init__(
        self,
        move_delay: float = 0.0,
        move_failures: int = 0,
        analysis_timeout: bool = False,
    ) -> None:
        self.move_delay = move_delay
        self.move_failures = move_failures
        self.analysis_timeout = analysis_timeout
        self.searches = 0
        self.analyses = 0

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        self.searches += 1
        if self.move_failures > 0:
            self.move_failures -= 1
            raise EngineUnavailableError("engine pool is saturated")
        if self.move_delay:
            await asyncio.sleep(self.move_delay)
        return next(iter(board.legal_moves)).uci()

    async def analyse(
        self,
        board: chess.Board,
        search_time: float | None = None,
        candidates: int | None = None,
    ) -> PositionAnalysis:
        self.analyses += 1
        if self.analysis_timeout:
            raise EngineSearchTimeoutError("no result within the analysis deadline")
        moves = [move.uci() for move in board.legal_moves][: candidates or 3]
        return PositionAnalysis(
            fen=board.fen(),
            side_to_move=PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK,
            depth=8,
            candidates=tuple(
                MoveCandidate(move=move, score=Score(centipawns=0), principal_variation=(move,)) for move in moves
            ),
        )


def e2e_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": UNREACHABLE_DATABASE_URL,
        "identity_salt": TEST_IDENTITY_SALT,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def build_client(
    session_factory: sessionmaker[Session],
    engine: FakeEngine | None = None,
    deadline: float = 4.5,
) -> httpx.AsyncClient:
    """Wire the app by hand: the lifespan would open its own database engine."""
    app = create_app(e2e_settings(webhook_deadline_seconds=deadline))
    app.state.session_factory = session_factory
    app.state.engine_pool = engine or FakeEngine()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def alice_request(
    message_id: int,
    session_id: str = "e2e-session-1",
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
        "application": {"application_id": "e2e-device-1"},
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


class AliceSession:
    """One Alice dialogue: carries the two state bags forward the way a device does."""

    def __init__(self, client: httpx.AsyncClient, session_id: str, user_id: str = USER_A, screen: bool = False) -> None:
        self._client = client
        self._session_id = session_id
        self._user_id = user_id
        self._screen = screen
        # The id of the last delivery, so a test can re-deliver exactly that one.
        self.message_id = 0
        self.user_state: dict[str, Any] = {}
        self.session_state: dict[str, Any] = {}

    async def say(self, command: str = "", new: bool = False) -> dict[str, Any]:
        self.message_id += 1
        return await self.resend(self.message_id, command, new=new)

    async def resend(self, message_id: int, command: str = "", new: bool = False) -> dict[str, Any]:
        """Deliver one request; re-delivering an earlier `message_id` is a retry."""
        payload = alice_request(
            message_id,
            session_id=self._session_id,
            user_id=self._user_id,
            command=command,
            new=new,
            state=self.user_state,
            session_state=self.session_state,
            screen=self._screen,
        )
        body = (await self._client.post("/alice/webhook", json=payload)).json()
        self.user_state = body.get("user_state_update") or self.user_state
        self.session_state = body.get("session_state") or {}
        return body


def context(owner: str, step: int, *, new: bool = False, session: str = "e2e") -> RequestContext:
    return RequestContext(
        skill_id="e2e",
        session_id=f"{session}-{owner}",
        message_id=str(step),
        fingerprint=f"{owner}-{step}".ljust(64, "0")[:64],
        is_new_session=new,
    )
