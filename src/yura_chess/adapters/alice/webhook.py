"""Alice webhook: protocol in, `GameService` out.

The adapter holds no chess logic. It pseudonymises the caller, turns the Alice
request triple into the replay key, checks that the `game_id` carried by the
client really belongs to this caller, and keeps the whole answer inside the
platform deadline. Which command an utterance means is decided by the injected
interpreter, not here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from hashlib import sha256
from time import monotonic
from typing import Literal

from fastapi import APIRouter
from fastapi import Request as HttpRequest
from pydantic import SecretStr

from yura_chess.adapters.alice.models import (
    CARD_TITLE_LIMIT,
    STATE_LIMIT_BYTES,
    TEXT_LIMIT,
    TTS_LIMIT,
    AliceRequest,
    AliceResponse,
    BigImageCard,
    ClarificationState,
    ConversationSessionState,
    GameStateUpdate,
    PendingActionState,
    ResponseBody,
)
from yura_chess.adapters.yandex_images import BoardImageService
from yura_chess.application.command_router import CommandKind, PendingClarification
from yura_chess.application.conversation import ConversationReply, ConversationService, ConversationState, PendingAction
from yura_chess.application.game_service import RequestContext
from yura_chess.application.player_identity import UnidentifiedRequestError, owner_key
from yura_chess.domain.results import TurnResult
from yura_chess.presentation.response_composer import compose_board_card
from yura_chess.storage.game_repository import ReplayFingerprintConflictError
from yura_chess.storage.models import FINGERPRINT_LENGTH

logger = logging.getLogger(__name__)

# What the card path leaves the webhook for serialising the answer it already has.
CARD_DEADLINE_MARGIN_SECONDS = 0.2


def build_router() -> APIRouter:
    router = APIRouter(tags=["alice"])

    @router.post("/alice/webhook", response_model=AliceResponse, response_model_exclude_none=True)
    async def webhook(payload: AliceRequest, http_request: HttpRequest) -> AliceResponse:
        settings = http_request.app.state.settings
        conversation = ConversationService(
            http_request.app.state.session_factory,
            http_request.app.state.engine_pool,
            settings,
        )
        images: BoardImageService | None = getattr(http_request.app.state, "board_images", None)
        started = monotonic()
        try:
            async with asyncio.timeout(settings.webhook_deadline_seconds):
                response, result, owner, context = await _handle(payload, conversation, settings.identity_salt)
                # The answer is already complete; the card is added only if the
                # rest of the budget can pay for it.
                remaining = settings.webhook_deadline_seconds - (monotonic() - started)
                response = await _attach_card(response, result, payload.has_screen, images, remaining)
                if owner is not None and context is not None:
                    conversation.store_response(
                        owner,
                        context,
                        response.model_dump_json(exclude_none=True),
                        result.game_id if result is not None else _response_game_id(response),
                    )
                return response
        except TimeoutError:
            # Whatever was committed stays committed; the next request resumes it.
            logger.warning("alice request exceeded the webhook deadline", extra={"session": payload.session.session_id})
            return _plain(payload, "Мне нужно чуть больше времени. Скажите «продолжаем».")

    return router


async def _handle(
    payload: AliceRequest,
    conversation: ConversationService,
    salt: SecretStr,
) -> tuple[AliceResponse, TurnResult | None, str | None, RequestContext | None]:
    try:
        owner = owner_key(salt, payload.user_id, payload.application_id)
    except UnidentifiedRequestError:
        # Without an identifier there is no owner, and without an owner no game
        # may be read or written.
        return (
            _plain(payload, "Не удалось определить пользователя. Попробуйте открыть навык ещё раз."),
            None,
            None,
            None,
        )

    context = RequestContext(
        skill_id=payload.session.skill_id,
        session_id=payload.session.session_id,
        message_id=str(payload.session.message_id),
        fingerprint=_fingerprint(payload),
        is_new_session=payload.session.new,
        timezone=payload.meta.timezone,
    )
    try:
        cached = conversation.cached_response(owner, context)
        if cached is not None:
            return AliceResponse.model_validate_json(cached), None, owner, context
        reply = await conversation.handle(owner, payload.request.command, context, _conversation_state(payload))
    except ReplayFingerprintConflictError:
        # Same replay key, different request: answer without touching the game.
        return _plain(payload, "Не расслышала. Повторите, пожалуйста."), None, None, None
    except LookupError:
        # A foreign or stale game_id must not reveal whether that game exists.
        reply = await conversation.handle(owner, "", context, ConversationState())

    return _compose(payload, reply), reply.turn, owner, context


def _response_game_id(response: AliceResponse) -> str | None:
    if response.user_state_update is not None:
        return response.user_state_update.game_id
    if response.session_state is not None:
        return response.session_state.game_id
    return None


async def _attach_card(
    response: AliceResponse,
    result: TurnResult | None,
    has_screen: bool,
    images: BoardImageService | None,
    remaining_seconds: float,
) -> AliceResponse:
    """Add the board picture when there is a screen, an image service and time left."""
    if result is None or images is None:
        return response
    card = compose_board_card(result, has_screen)
    if card is None:
        return response
    try:
        # A slow upload must expire before the webhook does, or a card that never
        # arrives would cost the player the move reply that is already composed.
        async with asyncio.timeout(remaining_seconds - CARD_DEADLINE_MARGIN_SECONDS):
            image_id = await images.image_id_for(card.position_hash, card.render, remaining_seconds)
    except Exception as error:  # noqa: BLE001 - the card is never worth failing the answer for
        logger.warning("board card skipped", extra={"error": type(error).__name__})
        return response
    if image_id is None:
        return response
    response.response.card = BigImageCard(image_id=image_id, title=_clip(card.title, CARD_TITLE_LIMIT))
    return response


def _claimed_game_id(payload: AliceRequest) -> str | None:
    """The game the client claims to own; ownership itself is checked in the service."""
    game_id = payload.state.user.get("game_id") or payload.state.session.get("game_id")
    return game_id if isinstance(game_id, str) and game_id else None


def _fingerprint(payload: AliceRequest) -> str:
    """Digest of the fields that decide what the request does.

    A retry of the same delivery reproduces them exactly; a different utterance
    or a different claimed game under the same replay key does not.
    """
    significant = json.dumps(
        {
            "command": payload.request.command,
            "original_utterance": payload.request.original_utterance,
            "type": payload.request.type,
            "new": payload.session.new,
            "game_id": _claimed_game_id(payload),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(significant.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def _compose(payload: AliceRequest, reply: ConversationReply) -> AliceResponse:
    text, pronunciation = reply.speech.text, reply.speech.tts
    return AliceResponse(
        response=ResponseBody(
            text=_clip(text, TEXT_LIMIT),
            # A separate `tts` is sent only when it differs from the display text.
            tts=_clip(pronunciation, TTS_LIMIT) if pronunciation is not None and pronunciation != text else None,
            end_session=False,
        ),
        user_state_update=_state_update(reply.turn),
        session_state=_session_state_update(reply.state),
        version=payload.version,
    )


def _plain(payload: AliceRequest, text: str) -> AliceResponse:
    """An answer that carries no game state, used when none may be disclosed."""
    return AliceResponse(
        response=ResponseBody(text=_clip(text, TEXT_LIMIT), end_session=False),
        version=payload.version,
    )


def _state_update(result: TurnResult | None) -> GameStateUpdate | None:
    if result is None:
        return None
    update = GameStateUpdate(game_id=result.game_id, revision=result.revision)
    if len(update.model_dump_json().encode("utf-8")) > STATE_LIMIT_BYTES:
        return None
    return update


def _conversation_state(payload: AliceRequest) -> ConversationState:
    raw = payload.state.session
    clarification_raw = raw.get("clarification")
    clarification = None
    if isinstance(clarification_raw, dict):
        heard = clarification_raw.get("heard")
        candidates = clarification_raw.get("candidates", [])
        if isinstance(heard, str) and isinstance(candidates, list):
            clarification = PendingClarification(heard[:255], tuple(str(item) for item in candidates[:16]))
    pending_action_raw = raw.get("pending_action")
    pending_action = None
    if isinstance(pending_action_raw, dict):
        kind = pending_action_raw.get("kind")
        utterance = pending_action_raw.get("utterance")
        if kind in {CommandKind.NEW_GAME.value, CommandKind.RESIGN.value, CommandKind.CONTINUE.value} and isinstance(
            utterance, str
        ):
            pending_action = PendingAction(CommandKind(kind), utterance[:255])
    last_reply_raw = raw.get("last_reply")
    page = raw.get("position_page", 0)
    return ConversationState(
        game_id=_claimed_game_id(payload),
        revision=raw.get("revision") if isinstance(raw.get("revision"), int) else None,
        last_heard=raw.get("last_heard") if isinstance(raw.get("last_heard"), str) else None,
        last_reply=last_reply_raw[:512] if isinstance(last_reply_raw, str) else None,
        clarification=clarification,
        pending_action=pending_action,
        position_page=page if isinstance(page, int) and 0 <= page <= 3 else 0,
    )


def _session_state_update(state: ConversationState) -> ConversationSessionState:
    clarification = (
        ClarificationState(heard=state.clarification.heard[:255], candidates=list(state.clarification.candidates[:16]))
        if state.clarification is not None
        else None
    )
    pending_action = None
    if state.pending_action is not None:
        pending_kind: Literal["new_game", "resign", "continue"]
        if state.pending_action.kind is CommandKind.NEW_GAME:
            pending_kind = "new_game"
        elif state.pending_action.kind is CommandKind.RESIGN:
            pending_kind = "resign"
        else:
            pending_kind = "continue"
        pending_action = PendingActionState(
            kind=pending_kind,
            utterance=state.pending_action.utterance[:255],
        )
    return ConversationSessionState(
        game_id=state.game_id,
        revision=state.revision,
        last_heard=state.last_heard[:255] if state.last_heard else None,
        last_reply=state.last_reply[:512] if state.last_reply else None,
        clarification=clarification,
        pending_action=pending_action,
        position_page=state.position_page,
    )


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
