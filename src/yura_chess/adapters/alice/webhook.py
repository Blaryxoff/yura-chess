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
    CardHeader,
    CardItem,
    ClarificationState,
    ConversationSessionState,
    GameStateUpdate,
    HelpSessionState,
    ItemsListCard,
    PendingActionState,
    RematchState,
    ResponseBody,
)
from yura_chess.adapters.yandex_images import BoardImageService
from yura_chess.application.command_router import (
    CommandKind,
    PendingClarification,
    RematchColor,
    RematchRequest,
    ReviewQuestion,
    ReviewRequest,
)
from yura_chess.application.conversation import ConversationReply, ConversationService, ConversationState, PendingAction
from yura_chess.application.game_service import RequestContext
from yura_chess.application.player_identity import UnidentifiedRequestError, owner_key
from yura_chess.domain.results import TurnResult
from yura_chess.presentation import help_speech
from yura_chess.presentation.response_composer import (
    CARD_DESCRIPTION_LIMIT,
    CARD_ITEMS_LIMIT,
    BoardCard,
    TextCard,
    compose_board_card,
)
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
                response, reply, owner, context = await _handle(payload, conversation, settings.identity_salt)
                # The answer is already complete; the card is added only if the
                # rest of the budget can pay for it.
                remaining = settings.webhook_deadline_seconds - (monotonic() - started)
                card = _card_for(reply, payload.has_screen)
                response = await _attach_card(response, card, images, remaining)
                if owner is not None and context is not None:
                    turn = reply.turn if reply is not None else None
                    conversation.store_response(
                        owner,
                        context,
                        response.model_dump_json(exclude_none=True),
                        turn.game_id if turn is not None else _response_game_id(response),
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
) -> tuple[AliceResponse, ConversationReply | None, str | None, RequestContext | None]:
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

    return _compose(payload, reply), reply, owner, context


def _response_game_id(response: AliceResponse) -> str | None:
    if response.user_state_update is not None:
        return response.user_state_update.game_id
    if response.session_state is not None:
        return response.session_state.game_id
    return None


def _card_for(reply: ConversationReply | None, has_screen: bool) -> BoardCard | TextCard | None:
    """The picture or listing this answer may show; a voice-only device gets none."""
    if reply is None or not has_screen:
        return None
    if reply.card is not None:
        return reply.card
    if reply.turn is None:
        return None
    orientation = reply.preferences.orientation_for(reply.turn.player_color) if reply.preferences else None
    return compose_board_card(reply.turn, has_screen, orientation)


def _text_card(card: TextCard) -> ItemsListCard:
    return ItemsListCard(
        header=CardHeader(text=_clip(card.header, CARD_TITLE_LIMIT)),
        items=[CardItem(description=_clip(item, CARD_DESCRIPTION_LIMIT)) for item in card.items[:CARD_ITEMS_LIMIT]],
    )


async def _attach_card(
    response: AliceResponse,
    card: BoardCard | TextCard | None,
    images: BoardImageService | None,
    remaining_seconds: float,
) -> AliceResponse:
    """Add the board picture when there is a screen, an image service and time left."""
    if card is None:
        return response
    if isinstance(card, TextCard):
        if card.items:
            response.response.card = _text_card(card)
        return response
    if images is None:
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


_PENDING_KINDS: dict[CommandKind, Literal["new_game", "resign", "continue", "rematch", "review", "puzzle"]] = {
    CommandKind.NEW_GAME: "new_game",
    CommandKind.RESIGN: "resign",
    CommandKind.CONTINUE: "continue",
    CommandKind.REMATCH: "rematch",
    CommandKind.REVIEW: "review",
    CommandKind.PUZZLE: "puzzle",
}


def _pending_action(raw: object) -> PendingAction | None:
    """Restore a confirmation the client is answering; anything unknown is dropped.

    A confirmation must come back as the very action that was asked about:
    reading an unrecognised kind as a plain «продолжить» would silently answer
    «да» to a different question than the one the player heard.
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    utterance = raw.get("utterance")
    if not isinstance(kind, str) or not isinstance(utterance, str):
        return None
    try:
        command = CommandKind(kind)
    except ValueError:
        return None
    if command not in _PENDING_KINDS:
        return None
    rematch_raw = raw.get("rematch")
    rematch = None
    if isinstance(rematch_raw, dict):
        color_raw = rematch_raw.get("color")
        try:
            color = RematchColor(color_raw) if isinstance(color_raw, str) else RematchColor.SAME
        except ValueError:
            color = RematchColor.SAME
        rematch = RematchRequest(color=color, harder=rematch_raw.get("harder") is True)
    review_raw = raw.get("review")
    review = None
    if isinstance(review_raw, str):
        try:
            review = ReviewRequest(ReviewQuestion(review_raw))
        except ValueError:
            review = None
    return PendingAction(command, utterance[:255], rematch=rematch, review=review)


def _conversation_state(payload: AliceRequest) -> ConversationState:
    raw = payload.state.session
    clarification_raw = raw.get("clarification")
    clarification = None
    if isinstance(clarification_raw, dict):
        heard = clarification_raw.get("heard")
        candidates = clarification_raw.get("candidates", [])
        if isinstance(heard, str) and isinstance(candidates, list):
            # Every candidate is a move phrase this skill produced; anything
            # else in the client state is foreign input and is not carried on.
            clarification = PendingClarification(
                heard[:255],
                tuple(item[:64] for item in candidates[:16] if isinstance(item, str)),
            )
    pending_action = _pending_action(raw.get("pending_action"))
    last_reply_raw = raw.get("last_reply")
    page = raw.get("position_page", 0)
    help_raw = raw.get("help")
    help_state = None
    if isinstance(help_raw, dict):
        topic = help_raw.get("topic")
        help_page = help_raw.get("page", 0)
        help_state = help_speech.restore(
            topic if isinstance(topic, str) else None,
            help_page if isinstance(help_page, int) else 0,
        )
    return ConversationState(
        game_id=_claimed_game_id(payload),
        revision=raw.get("revision") if isinstance(raw.get("revision"), int) else None,
        last_heard=raw.get("last_heard") if isinstance(raw.get("last_heard"), str) else None,
        last_reply=last_reply_raw[:512] if isinstance(last_reply_raw, str) else None,
        clarification=clarification,
        pending_action=pending_action,
        position_page=page if isinstance(page, int) and 0 <= page <= 3 else 0,
        help=help_state,
        reviewing=raw.get("reviewing") is True,
    )


def _session_state_update(state: ConversationState) -> ConversationSessionState:
    clarification = (
        ClarificationState(heard=state.clarification.heard[:255], candidates=list(state.clarification.candidates[:16]))
        if state.clarification is not None
        else None
    )
    pending_action = None
    if state.pending_action is not None:
        pending = state.pending_action
        pending_action = PendingActionState(
            kind=_PENDING_KINDS[pending.kind],
            utterance=pending.utterance[:255],
            rematch=(
                RematchState(color=pending.rematch.color, harder=pending.rematch.harder)
                if pending.rematch is not None
                else None
            ),
            review=pending.review.question if pending.review is not None else None,
        )
    session_state = ConversationSessionState(
        game_id=state.game_id,
        revision=state.revision,
        last_heard=state.last_heard[:255] if state.last_heard else None,
        last_reply=state.last_reply[:512] if state.last_reply else None,
        clarification=clarification,
        pending_action=pending_action,
        position_page=state.position_page,
        help=HelpSessionState(topic=state.help.topic, page=state.help.page) if state.help is not None else None,
        reviewing=state.reviewing,
    )
    return _within_state_limit(session_state)


def _within_state_limit(state: ConversationSessionState) -> ConversationSessionState:
    """Drop the repeatable parts first; navigation must survive the size limit.

    Losing `last_reply` costs a repeat, losing the candidates costs a
    clarification — losing the game id would lose the game.
    """
    for stripped in (state, state.model_copy(update={"last_reply": None})):
        if len(stripped.model_dump_json(exclude_none=True).encode("utf-8")) <= STATE_LIMIT_BYTES:
            return stripped
    return state.model_copy(update={"last_reply": None, "clarification": None, "pending_action": None})


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
