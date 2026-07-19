"""Alice protocol schema — only the fields this skill actually reads or sends.

Unknown fields are ignored rather than rejected: the platform adds fields over
time, and a skill that fails validation on them stops answering users.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Platform limits for a single response.
TEXT_LIMIT = 1024
TTS_LIMIT = 1024
STATE_LIMIT_BYTES = 1024
CARD_TITLE_LIMIT = 128

# `request_replays` stores the replay key in CHAR/VARCHAR(64) columns.
IDENTIFIER_LIMIT = 64


class _AliceModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Interfaces(_AliceModel):
    screen: dict[str, Any] | None = None


class Meta(_AliceModel):
    locale: str | None = None
    timezone: str | None = None
    interfaces: Interfaces = Field(default_factory=Interfaces)


class User(_AliceModel):
    user_id: str


class Application(_AliceModel):
    application_id: str


class Session(_AliceModel):
    message_id: int = Field(ge=0)
    session_id: str = Field(min_length=1, max_length=IDENTIFIER_LIMIT)
    skill_id: str = Field(min_length=1, max_length=IDENTIFIER_LIMIT)
    new: bool = False
    user: User | None = None
    application: Application | None = None


class Request(_AliceModel):
    command: str = ""
    original_utterance: str = ""
    type: str = "SimpleUtterance"


class State(_AliceModel):
    user: dict[str, Any] = Field(default_factory=dict)
    session: dict[str, Any] = Field(default_factory=dict)


class AliceRequest(_AliceModel):
    meta: Meta = Field(default_factory=Meta)
    session: Session
    request: Request = Field(default_factory=Request)
    state: State = Field(default_factory=State)
    version: str

    @property
    def has_screen(self) -> bool:
        return self.meta.interfaces.screen is not None

    @property
    def user_id(self) -> str | None:
        return self.session.user.user_id if self.session.user else None

    @property
    def application_id(self) -> str | None:
        return self.session.application.application_id if self.session.application else None


class BigImageCard(_AliceModel):
    """The single-image card; sent only to a device that has a screen."""

    type: str = "BigImage"
    image_id: str
    title: str | None = Field(default=None, max_length=CARD_TITLE_LIMIT)


class ResponseBody(_AliceModel):
    text: str
    tts: str | None = None
    card: BigImageCard | None = None
    end_session: bool = False


class GameStateUpdate(_AliceModel):
    """The only thing kept on the client: which game, and how far it has come."""

    game_id: str
    revision: int


class ClarificationState(_AliceModel):
    heard: str = Field(max_length=255)
    candidates: list[str] = Field(default_factory=list, max_length=16)


class PendingActionState(_AliceModel):
    kind: Literal["new_game", "resign"]
    utterance: str = Field(max_length=255)


class ConversationSessionState(_AliceModel):
    """Short-lived dialog state; the canonical game remains server-side."""

    last_heard: str | None = Field(default=None, max_length=255)
    last_reply: str | None = Field(default=None, max_length=512)
    clarification: ClarificationState | None = None
    pending_action: PendingActionState | None = None
    position_page: int = Field(default=0, ge=0, le=3)


class AliceResponse(_AliceModel):
    response: ResponseBody
    user_state_update: GameStateUpdate | None = None
    session_state: ConversationSessionState | None = None
    version: str
