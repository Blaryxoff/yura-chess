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
from collections.abc import Callable
from enum import StrEnum
from hashlib import sha256
from time import monotonic

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
    GameStateUpdate,
    ResponseBody,
)
from yura_chess.adapters.yandex_images import BoardImageService
from yura_chess.application.game_service import GameService, RequestContext
from yura_chess.application.player_identity import UnidentifiedRequestError, owner_key
from yura_chess.domain.results import TurnResult, TurnStatus
from yura_chess.presentation.response_composer import compose_board_card
from yura_chess.storage.game_repository import ReplayFingerprintConflictError
from yura_chess.storage.models import FINGERPRINT_LENGTH

logger = logging.getLogger(__name__)

# What the card path leaves the webhook for serialising the answer it already has.
CARD_DEADLINE_MARGIN_SECONDS = 0.2


class Intent(StrEnum):
    """The commands this adapter can dispatch on its own.

    Move recognition arrives with the command router; until then every request
    either opens a game or asks the current one to report itself.
    """

    START = "start"
    CONTINUE = "continue"


Interpreter = Callable[[AliceRequest, bool], Intent]


def default_interpreter(request: AliceRequest, has_game: bool) -> Intent:
    return Intent.CONTINUE if has_game else Intent.START


def build_router(interpreter: Interpreter = default_interpreter) -> APIRouter:
    router = APIRouter(tags=["alice"])

    @router.post("/alice/webhook", response_model=AliceResponse, response_model_exclude_none=True)
    async def webhook(payload: AliceRequest, http_request: HttpRequest) -> AliceResponse:
        settings = http_request.app.state.settings
        service = GameService(http_request.app.state.session_factory, http_request.app.state.engine_pool)
        images: BoardImageService | None = getattr(http_request.app.state, "board_images", None)
        started = monotonic()
        try:
            async with asyncio.timeout(settings.webhook_deadline_seconds):
                response, result = await _handle(payload, service, settings.identity_salt, interpreter)
                # The answer is already complete; the card is added only if the
                # rest of the budget can pay for it.
                remaining = settings.webhook_deadline_seconds - (monotonic() - started)
                return await _attach_card(response, result, payload.has_screen, images, remaining)
        except TimeoutError:
            # Whatever was committed stays committed; the next request resumes it.
            logger.warning("alice request exceeded the webhook deadline", extra={"session": payload.session.session_id})
            return _plain(payload, "Мне нужно чуть больше времени. Скажите «продолжаем».")

    return router


async def _handle(
    payload: AliceRequest,
    service: GameService,
    salt: SecretStr,
    interpreter: Interpreter,
) -> tuple[AliceResponse, TurnResult | None]:
    try:
        owner = owner_key(salt, payload.user_id, payload.application_id)
    except UnidentifiedRequestError:
        # Without an identifier there is no owner, and without an owner no game
        # may be read or written.
        return _plain(payload, "Не удалось определить пользователя. Попробуйте открыть навык ещё раз."), None

    context = RequestContext(
        skill_id=payload.session.skill_id,
        session_id=payload.session.session_id,
        message_id=str(payload.session.message_id),
        fingerprint=_fingerprint(payload),
    )
    game_id = _claimed_game_id(payload)

    try:
        if game_id is None or interpreter(payload, True) is Intent.START:
            result = await service.start_game(owner, context)
        else:
            result = await service.continue_game(owner, game_id, context)
    except ReplayFingerprintConflictError:
        # Same replay key, different request: answer without touching the game.
        return _plain(payload, "Не расслышала. Повторите, пожалуйста."), None
    except LookupError:
        # A foreign or stale game_id must not reveal whether that game exists.
        result = await service.start_game(owner, context)

    return _compose(payload, result), result


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
    game_id = payload.state.user.get("game_id")
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


def _compose(payload: AliceRequest, result: TurnResult) -> AliceResponse:
    text, pronunciation = _speak(result)
    return AliceResponse(
        response=ResponseBody(
            text=_clip(text, TEXT_LIMIT),
            # A separate `tts` is sent only when it differs from the display text.
            tts=_clip(pronunciation, TTS_LIMIT) if pronunciation is not None and pronunciation != text else None,
            end_session=False,
        ),
        user_state_update=_state_update(result),
        version=payload.version,
    )


def _plain(payload: AliceRequest, text: str) -> AliceResponse:
    """An answer that carries no game state, used when none may be disclosed."""
    return AliceResponse(
        response=ResponseBody(text=_clip(text, TEXT_LIMIT), end_session=False),
        version=payload.version,
    )


def _state_update(result: TurnResult) -> GameStateUpdate | None:
    update = GameStateUpdate(game_id=result.game_id, revision=result.revision)
    if len(update.model_dump_json().encode("utf-8")) > STATE_LIMIT_BYTES:
        return None
    return update


def _speak(result: TurnResult) -> tuple[str, str | None]:
    """Placeholder wording; the speech layer replaces it with real phrasing."""
    if result.status is TurnStatus.ENGINE_UNAVAILABLE:
        return "Я ещё думаю над ответом. Скажите «продолжаем».", None
    if result.status is TurnStatus.GAME_OVER:
        return "Партия окончена.", None
    if result.engine_move:
        return f"Мой ход: {result.engine_move}.", f"Мой ход: {' '.join(result.engine_move)}."
    return "Ваш ход.", None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
