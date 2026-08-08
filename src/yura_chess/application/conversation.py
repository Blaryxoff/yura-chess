"""Voice conversation orchestration shared by Alice and the local shell."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import chess
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import (
    MAX_LEVEL,
    CommandKind,
    LevelIntent,
    PendingClarification,
    PreferenceChange,
    PuzzleQuestion,
    PuzzleRequest,
    RematchColor,
    RematchRequest,
    ReviewQuestion,
    ReviewRequest,
    RoutedCommand,
    ScreenWish,
    TrainingQuestion,
    TrainingRequest,
    confirmation_answer,
    contains_multiple_moves,
    parse_level_value,
    route,
)
from yura_chess.application.game_service import (
    GameService,
    LevelChange,
    LevelChangeStatus,
    MoveSearch,
    RequestContext,
)
from yura_chess.application.puzzle_service import OpenPuzzle, PuzzleService
from yura_chess.application.review_service import ReviewService
from yura_chess.application.training_service import PositionSearch, TrainingService
from yura_chess.domain.game import EngineSettings, GameMode, GameState, GameStatus, PlayerColor
from yura_chess.domain.preferences import (
    BoardOrientation,
    DetailLevel,
    NotationStyle,
    PauseStyle,
    PlayerPreferences,
)
from yura_chess.domain.results import GameEnd, TurnResult, TurnStatus
from yura_chess.presentation import help_speech
from yura_chess.presentation.commentary import comment_on
from yura_chess.presentation.game_facts import answer_game_fact
from yura_chess.presentation.help_speech import HelpAnswer, HelpMode, HelpState
from yura_chess.presentation.move_speech import (
    PLAYER_MOVE_PREFIX,
    SoundEvent,
    SoundLibrary,
    Speech,
    add_move_sounds,
    add_pauses,
    describe_move,
)
from yura_chess.presentation.position_speech import answer_position_query, describe_recent_moves
from yura_chess.presentation.response_composer import (
    BoardCard,
    TextCard,
    compose_help_card,
    compose_pgn_card,
    compose_position_card,
    compose_turn,
)
from yura_chess.settings import Settings
from yura_chess.storage.database import run_transaction_with_deadlock_retry, session_scope
from yura_chess.storage.preferences_repository import PreferencesRepository
from yura_chess.storage.review_repository import ReviewRepository
from yura_chess.storage.transcript_repository import TranscriptRepository
from yura_chess.storage.usage_repository import UsageRepository
from yura_chess.storage.usage_repository import request_key as usage_request_key
from yura_chess.voice.move_resolver import recognize
from yura_chess.voice.normalizer import normalize

MAX_SKILL_LEVEL = MAX_LEVEL
# One rematch step up is two of the twenty engine levels: less is not audible.
REMATCH_LEVEL_STEP = 2

_BLACK = re.compile(r"\bчерн")
_LEVEL_SCALE_ANSWER = (
    "Чем больше число, тем сильнее я играю. Ноль — самый легкий уровень, двадцать — самый сильный. "
    "Чтобы поставить, скажите: «уровень пять»."
)
_SCREEN_BIGGER_ANSWER = "На весь экран переключить не могу. Читаю доску."
_SCREEN_BIGGER_NO_GAME = (
    "На весь экран переключить не могу. Зато я читаю доску вслух. Скажите «новая игра», и я буду называть каждый ход."
)
_SCREEN_TAP_ANSWER = (
    "Нажимать на клетки здесь нельзя, ходы я принимаю только голосом. Скажите, какая фигура и на какую клетку идет."
)
_SCREEN_TAP_NO_GAME = "Нажимать на клетки здесь нельзя, ходы я принимаю только голосом. Скажите «новая игра», и начнем."
_TRAINER_OFFER = (
    "Сейчас играем без подсказок. Включить режим тренера и ответить на ваш вопрос? "
    "Это уже не честная партия. Скажите «да» или «нет»."
)
_LEVEL_NO_GAME = "Партии сейчас нет. Скажите: «новая игра, уровень пять»."
_LEVEL_GAME_OVER = "Партия закончена. В новой партии скажите: «новая игра, уровень три»."
_MONTHS = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def _new_session_welcome(speech: Speech) -> Speech:
    return Speech.of(f"{help_speech.SKILL_INTRO} Чтобы услышать инструкцию и команды, скажите «помощь». {speech.text}")


@dataclass(frozen=True, slots=True)
class ConversationState:
    game_id: str | None = None
    revision: int | None = None
    last_heard: str | None = None
    last_reply: str | None = None
    clarification: PendingClarification | None = None
    pending_action: PendingAction | None = None
    position_page: int = 0
    # Where the open help stopped reading; `None` while help is closed.
    help: HelpState | None = None
    # True while a review is being read, so «дальше» turns its page rather than
    # the board's. The durable cursor itself lives server-side.
    reviewing: bool = False


@dataclass(frozen=True, slots=True)
class PendingAction:
    kind: CommandKind
    utterance: str
    # A rematch keeps the colour and level it was asked for; re-reading the
    # utterance after the confirmation would lose them.
    rematch: RematchRequest | None = None
    # Which review question is waiting for a yes; only the training branch asks.
    review: ReviewRequest | None = None
    # The coaching question asked in an honest game, answered once the trainer is on.
    training: TrainingRequest | None = None


@dataclass(frozen=True, slots=True)
class ConversationReply:
    speech: Speech
    state: ConversationState
    turn: TurnResult | None = None
    # The preferences this answer was rendered with, so the transport can draw
    # the board from the side the player chose.
    preferences: PlayerPreferences | None = None
    # An optional screen card the transport may attach; the speech above is
    # always complete without it. A puzzle position arrives here because it
    # belongs to no game and so has no `turn`.
    card: BoardCard | TextCard | None = None
    # Only explicit skill-exit commands end the Alice session. The game remains
    # server-side and can be resumed on the next launch.
    end_session: bool = False
    # The cue for the answer as a whole — an opened game, a solved puzzle;
    # `handle` applies it after pauses and settings.
    sound: SoundEvent | None = None
    # The cue for the player's own move, sounded at the front and only when the
    # answer names that move.
    player_sound: SoundEvent | None = None
    # The cue for the engine's move, sounded where «Мой ход» begins, so that an
    # answer carrying both plies is heard as both.
    engine_sound: SoundEvent | None = None


class ChessEngine(MoveSearch, PositionSearch, Protocol):
    """Both engine capabilities the conversation needs; `StockfishPool` has them."""


class ConversationService:
    """Interpret one utterance and produce the complete voice-first response."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: ChessEngine,
        settings: Settings,
        puzzles: PuzzleService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._training = TrainingService(session_factory, engine, settings)
        self._review = ReviewService(session_factory, engine, settings)
        # Injectable so a test can fix which puzzle the catalogue offers.
        self._puzzles = puzzles or PuzzleService(session_factory)
        self._games = GameService(session_factory, engine, observer=self._training)
        self._settings = settings
        self._sounds = SoundLibrary(
            start=settings.alice_sound_start,
            move=settings.alice_sound_move,
            check=settings.alice_sound_check,
            checkmate=settings.alice_sound_checkmate,
            success=settings.alice_sound_success,
        )

    async def handle(
        self,
        owner_key: str,
        utterance: str,
        request: RequestContext,
        state: ConversationState | None = None,
    ) -> ConversationReply:
        prior_state = state or ConversationState()
        preferences = self._preferences(owner_key)
        replayed_puzzle = self._puzzles.replayed_response(owner_key, request)
        if replayed_puzzle is not None:
            replay_state = replace(
                prior_state,
                last_heard=route(utterance).normalized.text or prior_state.last_heard,
                clarification=None,
                position_page=0,
                help=None,
                reviewing=False,
            )
            reply = self._with_puzzle_card(
                owner_key,
                ConversationReply(replayed_puzzle.speech, replay_state, sound=replayed_puzzle.sound),
                preferences,
            )
        else:
            replayed_level = self._games.replayed_level_change(owner_key, request)
            replayed = None if replayed_level is not None else await self._games.resume_request(owner_key, request)
            if replayed_level is not None:
                # The original request cleared these; a retry must hand back the same state.
                reply = self._level_change_reply(
                    replayed_level,
                    replace(
                        prior_state,
                        last_heard=route(utterance).normalized.text or prior_state.last_heard,
                        clarification=None,
                        position_page=0,
                        help=None,
                        reviewing=False,
                        pending_action=None,
                    ),
                )
            elif replayed is not None:
                reply = self._replayed_turn_reply(owner_key, utterance, replayed, prior_state, preferences)
            else:
                reply = await self._handle(owner_key, utterance, request, prior_state, preferences)
        if route(utterance).kind not in {CommandKind.REPEAT_REPLY, CommandKind.REPEAT_SLOW}:
            # Stored before the pauses are added, so a repeat reads words rather
            # than speech markup.
            reply = replace(reply, state=replace(reply.state, last_reply=reply.speech.spoken()[:512]))
        # A settings command answers with the preferences it just stored.
        effective = reply.preferences or preferences
        speech = add_pauses(reply.speech, effective.pause_style)
        speech = add_move_sounds(
            speech,
            reply.sound,
            reply.player_sound,
            reply.engine_sound,
            effective.sounds_enabled,
            self._sounds,
        )
        return replace(reply, speech=speech, preferences=effective)

    def _preferences(self, owner_key: str) -> PlayerPreferences:
        with session_scope(self._session_factory) as session:
            return PreferencesRepository(session).load(owner_key)

    def _save_preference(self, owner_key: str, change: PreferenceChange) -> PlayerPreferences:
        def save(session: Session) -> PlayerPreferences:
            repository = PreferencesRepository(session)
            current = repository.load(owner_key, for_update=True)
            return repository.save(change.apply(current))

        return run_transaction_with_deadlock_retry(self._session_factory, save)

    def cached_response(self, owner_key: str, request: RequestContext) -> str | None:
        return self._games.cached_alice_response(owner_key, request)

    def store_response(
        self,
        owner_key: str,
        request: RequestContext,
        response_payload: str,
        game_id: str | None,
    ) -> None:
        self._games.store_alice_response(owner_key, request, response_payload, game_id)

    async def _handle(
        self,
        owner_key: str,
        utterance: str,
        request: RequestContext,
        state: ConversationState,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        state = state or ConversationState()
        game = self._load(owner_key, state.game_id)
        if state.game_id is not None and game is None:
            # A game id the owner cannot load is someone else's or long gone; the
            # player keeps their own running game instead of silently getting a new one.
            state = ConversationState(last_heard=state.last_heard)
            game = self._games.find_latest_active_game(owner_key)
            if game is not None:
                state = self._with_game(state, game)

        if request.is_new_session and not utterance.strip() and not self._games.request_was_seen(owner_key, request):
            # An unfinished puzzle is asked about as a puzzle, never as a game.
            unsolved = self._puzzles.find_open(owner_key)
            if unsolved is not None:
                self._record(owner_key, route(""), None, request, state.pending_action)
                return self._with_puzzle_card(
                    owner_key,
                    ConversationReply(
                        _new_session_welcome(self._puzzles.resume_prompt(unsolved)),
                        replace(state, pending_action=PendingAction(CommandKind.PUZZLE, "")),
                    ),
                    preferences,
                )
            candidate = game if game is not None and game.status is GameStatus.ACTIVE else None
            candidate = candidate or self._games.find_latest_active_game(owner_key)
            if candidate is not None:
                self._record(owner_key, route(""), None, request, state.pending_action)
                prompt_state = replace(
                    self._with_game(state, candidate),
                    pending_action=PendingAction(CommandKind.CONTINUE, ""),
                )
                return ConversationReply(
                    _new_session_welcome(self._resume_prompt(candidate, request.timezone)),
                    prompt_state,
                )

        board = game.board() if game is not None else None
        routed = route(
            utterance,
            board,
            pending=state.clarification,
            last_heard=state.last_heard,
            confidence_threshold=self._settings.voice_move_confidence_threshold,
        )
        open_puzzle = self._puzzles.find_open(owner_key)
        mode = _help_mode(game, open_puzzle is not None)
        help_navigation = (
            help_speech.navigate(utterance, state.help, mode) or help_speech.bare_topic(utterance, mode)
            if state.help is not None
            else None
        )
        review_step = (
            _review_step(routed.normalized.text) if state.reviewing and routed.kind is not CommandKind.REVIEW else None
        )
        interpreted_kind = (
            CommandKind.HELP
            if help_navigation is not None
            else CommandKind.REVIEW
            if review_step is not None
            else routed.kind
        )
        self._record(owner_key, routed, board, request, state.pending_action, interpreted_kind)

        repeated = {CommandKind.REPEAT_HEARD, CommandKind.REPEAT_SLOW}
        next_heard = state.last_heard if routed.kind in repeated else routed.normalized.text
        next_state = replace(
            state,
            last_heard=next_heard or state.last_heard,
            clarification=None,
            position_page=0,
            help=None,
            reviewing=False,
        )

        if routed.kind is CommandKind.PLATFORM:
            saved = " Партия сохранена." if game is not None and game.status is GameStatus.ACTIVE else ""
            return ConversationReply(
                Speech.of(f"Это команда Алисе, а не шахматный ход.{saved} Выхожу из навыка — повторите команду Алисе."),
                replace(next_state, pending_action=None),
                end_session=True,
            )

        if routed.kind is CommandKind.EXIT:
            saved = " Партия сохранена." if game is not None and game.status is GameStatus.ACTIVE else ""
            return ConversationReply(
                Speech.of(f"До свидания.{saved}"),
                replace(next_state, pending_action=None),
                end_session=True,
            )

        if routed.kind is CommandKind.EXIT_CONFIRM:
            asked = self._with_game(next_state, game) if game is not None else next_state
            return ConversationReply(
                Speech.of("Выйти из навыка? Скажите «да» или «нет»."),
                replace(asked, pending_action=PendingAction(CommandKind.EXIT_CONFIRM, utterance[:255])),
            )

        if state.pending_action is not None and routed.kind is CommandKind.HELP:
            return self._help_reply(
                help_speech.answer_help(utterance, mode, state.help),
                replace(next_state, pending_action=None),
                game,
            )

        pending_action = state.pending_action
        pending_confirmation = (
            confirmation_answer(utterance, pending_action.kind) if pending_action is not None else None
        )

        if routed.kind is CommandKind.REPEAT_REPLY:
            speech = (
                Speech.of(state.last_reply) if state.last_reply is not None else Speech.of("Пока нечего повторять.")
            )
            return ConversationReply(speech, state)

        if help_navigation is None and routed.kind in {
            CommandKind.ATTENTION,
            CommandKind.SOCIAL,
            CommandKind.PAUSE,
            CommandKind.AMBIGUOUS_TURN,
            CommandKind.ORIENTATION_QUERY,
            CommandKind.NAVIGATE_BACK,
        }:
            return ConversationReply(
                _conversational_reply(
                    routed.kind,
                    routed.normalized.text,
                    game,
                    open_puzzle is not None,
                    pending_action,
                ),
                state,
            )
        if (
            pending_action is not None
            and pending_action.kind is CommandKind.CONTINUE
            and routed.kind is CommandKind.MOVE
        ):
            candidate = game or self._games.find_latest_active_game(owner_key)
            if candidate is not None and routed.move is not None:
                result = await self._games.play_move(owner_key, candidate.id, routed.move, request)
                reply = self._turn_reply(
                    owner_key,
                    result,
                    replace(next_state, pending_action=None),
                    preferences,
                    echo_player_move=True,
                )
                return self._with_training_warning(owner_key, reply)

        if (
            pending_action is not None
            and pending_action.kind is CommandKind.CONTINUE
            and routed.kind
            in {
                CommandKind.START,
                CommandKind.NEW_GAME,
            }
            and pending_confirmation is None
        ):
            return await self._start(
                owner_key,
                utterance,
                request,
                replace(next_state, pending_action=None),
                preferences,
            )

        if (
            pending_action is not None
            and pending_action.kind is CommandKind.CONTINUE
            and routed.kind in {CommandKind.ILLEGAL_MOVE, CommandKind.CLARIFY}
        ):
            next_state = replace(next_state, pending_action=None)
            pending_action = None

        pending_overrides = {
            CommandKind.PREFERENCE,
            CommandKind.PUZZLE,
            CommandKind.REMATCH,
            CommandKind.COLOR_CHOICE,
            CommandKind.REVIEW,
            CommandKind.TRAINING,
            CommandKind.LEVEL_QUERY,
            CommandKind.LEVEL,
            CommandKind.GAME_FACT,
            CommandKind.POSITION_QUERY,
            CommandKind.REPEAT_HEARD,
            CommandKind.REPEAT_SLOW,
            CommandKind.BOARD_SETUP,
            CommandKind.SCREEN,
        }
        if pending_action is not None and routed.kind in pending_overrides:
            next_state = replace(next_state, pending_action=None)
            pending_action = None

        if pending_action is not None:
            confirmation = (
                True
                if pending_action.kind is CommandKind.CONTINUE and routed.kind is CommandKind.CONTINUE
                else pending_confirmation
            )
            if confirmation is None:
                return ConversationReply(Speech.of("Скажите «да» или «нет»."), next_state)
            confirmed = pending_action
            next_state = replace(next_state, pending_action=None)
            if not confirmation:
                cancelled_state = self._with_game(next_state, game) if game else next_state
                if confirmed.kind is CommandKind.PUZZLE:
                    unsolved = self._puzzles.find_open(owner_key)
                    if unsolved is not None:
                        self._puzzles.abandon(owner_key, unsolved)
                    return ConversationReply(
                        Speech.of("Хорошо, задачу закрываю. Скажите «продолжить» или «новая игра»."),
                        cancelled_state,
                    )
                if confirmed.kind is CommandKind.CONTINUE:
                    return ConversationReply(
                        Speech.of("Хорошо. Скажите «новая игра», если хотите начать другую."),
                        cancelled_state,
                    )
                if confirmed.kind is CommandKind.TRAINING:
                    return ConversationReply(Speech.of("Хорошо. Продолжаем без подсказок."), cancelled_state)
                return ConversationReply(Speech.of("Хорошо, отменяю."), cancelled_state)
            if confirmed.kind is CommandKind.TRAINING and confirmed.training is not None and game is not None:
                return await self._enable_trainer_and_answer(owner_key, game, confirmed.training, request, next_state)
            if confirmed.kind is CommandKind.PUZZLE:
                unsolved = self._puzzles.find_open(owner_key)
                if unsolved is not None:
                    return self._with_puzzle_card(
                        owner_key,
                        ConversationReply(self._puzzles.present(unsolved).speech, next_state),
                        preferences,
                    )
                return ConversationReply(Speech.of("Задача уже закрыта. Скажите «дай задачу»."), next_state)
            if confirmed.kind is CommandKind.REMATCH and confirmed.rematch is not None:
                base = game or self._games.find_latest_game(owner_key)
                if base is not None:
                    return await self._rematch(owner_key, base, request, confirmed.rematch, next_state, preferences)
            if confirmed.kind is CommandKind.REVIEW:
                base = self._reviewable(owner_key, game)
                if base is not None:
                    branch_id, speech = await self._review.start_branch(owner_key, base)
                    branch = self._load(owner_key, branch_id) if branch_id is not None else None
                    return ConversationReply(
                        speech,
                        self._with_game(next_state, branch) if branch is not None else next_state,
                        sound=SoundEvent.START if branch is not None else None,
                    )
            if confirmed.kind is CommandKind.EXIT_CONFIRM:
                saved = " Партия сохранена." if game is not None and game.status is GameStatus.ACTIVE else ""
                return ConversationReply(Speech.of(f"До свидания.{saved}"), next_state, end_session=True)
            if confirmed.kind is CommandKind.NEW_GAME:
                return await self._start(owner_key, confirmed.utterance, request, next_state, preferences)
            if confirmed.kind is CommandKind.RESIGN and game is not None:
                unsolved = self._puzzles.find_open(owner_key)
                if unsolved is not None:
                    self._puzzles.abandon(owner_key, unsolved)
                result = await self._games.resign(owner_key, game.id, request)
                return self._turn_reply(owner_key, result, next_state, preferences)
            if confirmed.kind is CommandKind.CONTINUE:
                candidate = game or self._games.find_latest_active_game(owner_key)
                if candidate is not None:
                    result = await self._games.continue_game(owner_key, candidate.id, request)
                    return self._turn_reply(owner_key, result, next_state, preferences)

        if routed.kind is CommandKind.CANCEL_CLARIFY:
            if state.clarification is not None:
                return ConversationReply(
                    Speech.of("Хорошо, ход не делаю. Назовите другой ход."),
                    self._with_game(next_state, game) if game is not None else next_state,
                )
            # Nothing is waiting on an answer, so «отмена» is about the board: it
            # takes back the last full move, the way «отмени ход» does.
            takes_back = (
                game is not None
                and game.status is GameStatus.ACTIVE
                and game.last_player_move_at is not None
                and open_puzzle is None
                and state.help is None
            )
            if takes_back and game is not None:
                return await self._undo(owner_key, game, request, next_state, 1)
            return ConversationReply(
                Speech.of("Хорошо, ничего не меняю. Назовите команду или попросите помощь."),
                self._with_game(next_state, game) if game is not None else next_state,
            )

        # Open help owns «дальше», «назад» and «сначала»: otherwise they would be
        # read as board pagination, as a new game, or as a step of the puzzle.
        if state.help is not None:
            if help_navigation is not None:
                return self._help_reply(help_navigation, next_state, game)

        # An open puzzle owns moves and hints: they are judged against its own
        # position, so they must not reach the game's move resolution at all.
        if open_puzzle is not None:
            solving = self._puzzle_reply(owner_key, open_puzzle, utterance, request, state, next_state)
            if solving is not None:
                return self._with_puzzle_card(owner_key, solving, preferences)
        # An open review owns «дальше» and «назад» while it is being dictated,
        # exactly as open help owns them.
        if state.reviewing and routed.kind is not CommandKind.REVIEW:
            reviewed = self._reviewable(owner_key, game)
            if reviewed is not None and review_step is not None:
                return ConversationReply(
                    self._review.dictate(owner_key, reviewed, review_step),
                    replace(self._with_game(next_state, reviewed), reviewing=True),
                )
        if routed.kind is CommandKind.REVIEW and routed.review is not None:
            return await self._review_reply(owner_key, game, routed.review, utterance, next_state)
        if routed.kind is CommandKind.HELP_EXIT:
            return self._help_reply(help_speech.close(), next_state, game)
        if routed.kind is CommandKind.HELP:
            return self._help_reply(help_speech.answer_help(utterance, mode, state.help), next_state, game)

        # Settings and rematch answer the same way with or without a game open.
        if routed.kind is CommandKind.PREFERENCE and routed.preference is not None:
            updated = self._save_preference(owner_key, routed.preference)
            return ConversationReply(
                Speech.of(_preference_confirmation(routed.preference)),
                self._with_game(next_state, game) if game is not None else next_state,
                preferences=updated,
            )
        if routed.kind is CommandKind.BOARD_SETUP:
            if game is None:
                started = await self._start(owner_key, "", request, next_state, preferences)
                return replace(
                    started,
                    speech=Speech.of(
                        "Хорошо, расставляйте фигуры. Вы играете белыми, я черными. "
                        "Когда будете готовы, назовите только свой ход. Мои ходы я буду объявлять."
                    ),
                )
            side = "черными" if game.player_color is PlayerColor.BLACK else "белыми"
            opponent = "белыми" if game.player_color is PlayerColor.BLACK else "черными"
            return ConversationReply(
                Speech.of(
                    f"Хорошо, расставляйте фигуры. Вы играете {side}, я {opponent}. "
                    "Когда будете готовы, назовите только свой ход. Мои ходы я буду объявлять."
                ),
                self._with_game(next_state, game),
            )
        if routed.kind is CommandKind.COLOR_CHOICE and routed.rematch is not None:
            # The session may have lost the game the player is in; a game found
            # server-side is still theirs and may not be ended without an answer.
            if game is None:
                game = self._games.find_latest_active_game(owner_key)
            if game is None or game.status is not GameStatus.ACTIVE:
                base = game or self._games.find_latest_game(owner_key)
                if base is None:
                    return await self._start(owner_key, utterance, request, next_state, preferences)
                return await self._rematch(owner_key, base, request, routed.rematch, next_state, preferences)
            requested = _rematch_color(game.player_color, routed.rematch.color)
            if game.player_color is requested:
                side = "черными" if requested is PlayerColor.BLACK else "белыми"
                # The engine still owes an answer, so the next word is not a move.
                tail = "Назовите ход." if game.pending_engine_turn is None else "Скажите «продолжаем»."
                return ConversationReply(
                    Speech.of(f"Вы и так играете {side}. {tail}"),
                    self._with_game(next_state, game),
                )
            if game.last_player_move_at is None:
                return await self._switch_color(owner_key, game, request, requested, next_state, preferences)
            return ConversationReply(
                Speech.of(_color_switch_question(game, requested)),
                replace(
                    self._with_game(next_state, game),
                    pending_action=PendingAction(CommandKind.REMATCH, utterance[:255], routed.rematch),
                ),
            )
        if routed.kind is CommandKind.REMATCH and routed.rematch is not None:
            base = game or self._games.find_latest_game(owner_key)
            if base is None:
                return ConversationReply(
                    Speech.of("Партии еще не было, реванш играть не с чем. Скажите «новая игра»."),
                    next_state,
                )
            if base.status is GameStatus.ACTIVE:
                return ConversationReply(
                    Speech.of("Текущая партия еще идет. Начать новую? Скажите «да» или «нет»."),
                    replace(
                        self._with_game(next_state, base),
                        pending_action=PendingAction(CommandKind.REMATCH, utterance[:255], routed.rematch),
                    ),
                )
            return await self._rematch(owner_key, base, request, routed.rematch, next_state, preferences)

        # Puzzles need no game, and never change the one that happens to be open.
        if routed.kind is CommandKind.PUZZLE and routed.puzzle is not None:
            chosen = self._puzzles.answer(owner_key, routed.puzzle, request, None)
            return self._with_puzzle_card(
                owner_key,
                ConversationReply(
                    chosen.speech,
                    self._with_game(next_state, game) if game is not None else next_state,
                    sound=chosen.sound,
                ),
                preferences,
            )

        if routed.kind is CommandKind.LEVEL and routed.level is not None:
            if routed.level.intent is LevelIntent.SCALE:
                return ConversationReply(
                    Speech.of(_LEVEL_SCALE_ANSWER),
                    self._with_game(next_state, game) if game is not None else next_state,
                )
            if open_puzzle is not None:
                return ConversationReply(
                    Speech.of("Сейчас открыта задача. Скажите «вернуться к партии», потом назовите уровень."),
                    self._with_game(next_state, game) if game is not None else next_state,
                )
            if routed.level.level is None:
                if game is None:
                    return ConversationReply(Speech.of(_LEVEL_NO_GAME), next_state)
                if game.status is not GameStatus.ACTIVE:
                    return ConversationReply(Speech.of(_LEVEL_GAME_OVER), self._with_game(next_state, game))
                return ConversationReply(
                    Speech.of(
                        "Да. Назовите уровень от нуля до двадцати, например: «уровень пять». "
                        "Партия продолжится с той же позиции."
                    ),
                    self._with_game(next_state, game),
                )
            if game is None:
                return await self._start(owner_key, utterance, request, next_state, preferences)
            change = self._games.set_level(owner_key, game.id, routed.level.level, request)
            return self._level_change_reply(change, next_state)

        if routed.kind is CommandKind.TRAINING and routed.training is not None:
            if game is None:
                return ConversationReply(
                    Speech.of("Партии еще нет, тренировать нечего. Скажите «новая игра»."),
                    next_state,
                )
            if _needs_trainer_consent(game, routed.training):
                return ConversationReply(
                    Speech.of(_TRAINER_OFFER),
                    replace(
                        self._with_game(next_state, game),
                        pending_action=PendingAction(
                            CommandKind.TRAINING,
                            routed.normalized.text,
                            training=routed.training,
                        ),
                    ),
                )
            speech = await self._training.answer(owner_key, game, routed.training, request)
            return ConversationReply(speech, self._with_game(next_state, self._reload(owner_key, game)))

        if routed.kind is CommandKind.BACKCHANNEL:
            return ConversationReply(
                _conversational_reply(
                    routed.kind,
                    routed.normalized.text,
                    game,
                    open_puzzle is not None,
                    None,
                ),
                self._with_game(next_state, game) if game is not None else next_state,
            )

        if routed.kind is CommandKind.SCREEN and routed.screen is not None:
            if routed.screen.wish is ScreenWish.TAP:
                playable = game is not None and game.status is GameStatus.ACTIVE
                return ConversationReply(
                    Speech.of(_SCREEN_TAP_ANSWER if playable else _SCREEN_TAP_NO_GAME),
                    self._with_game(next_state, game) if game is not None else next_state,
                )
            if board is None or game is None:
                return ConversationReply(Speech.of(_SCREEN_BIGGER_NO_GAME), next_state)
            read = answer_position_query("какая позиция", board, 0)
            return ConversationReply(
                Speech.of(f"{_SCREEN_BIGGER_ANSWER} {read.speech.text}"),
                replace(self._with_game(next_state, game), position_page=read.page),
            )

        if routed.kind is CommandKind.WHY:
            if game is not None and game.mode is GameMode.TRAINING and game.moves:
                speech = await self._training.answer(
                    owner_key,
                    game,
                    TrainingRequest(TrainingQuestion.WHY_MOVE),
                    request,
                )
                return ConversationReply(speech, self._with_game(next_state, game))
            return ConversationReply(
                Speech.of("Что именно объяснить: последний ход, позицию или правило?"),
                self._with_game(next_state, game) if game is not None else next_state,
            )

        if routed.kind is CommandKind.DONT_KNOW:
            return ConversationReply(
                Speech.of("Ничего страшного. Скажите «помощь», и я подскажу, что можно сделать дальше."),
                self._with_game(next_state, game) if game is not None else next_state,
            )

        if game is None:
            if routed.kind is CommandKind.GAME_FACT:
                return ConversationReply(
                    Speech.of("Партии еще нет, поэтому рассказать о ней нечего. Скажите «новая игра»."),
                    next_state,
                )
            if routed.kind is CommandKind.LEVEL_QUERY:
                level = self._settings.engine_skill_level
                hint = _hint(preferences, "Чтобы выбрать другой, скажите: «новая игра, уровень пять».")
                return ConversationReply(
                    Speech.of(f"Уровень сложности по умолчанию — {level} из 20.{hint}"),
                    next_state,
                )
            if routed.kind is CommandKind.CONTINUE:
                candidate = self._games.find_latest_active_game(owner_key)
                if candidate is None:
                    return ConversationReply(Speech.of("Незаконченных партий нет. Скажите «новая игра»."), next_state)
                result = await self._games.continue_game(owner_key, candidate.id, request)
                return self._turn_reply(owner_key, result, next_state, preferences)
            return await self._start(owner_key, utterance, request, next_state, preferences)

        assert board is not None
        if not utterance.strip():
            result = await self._games.continue_game(owner_key, game.id, request)
            return self._turn_reply(owner_key, result, next_state, preferences)
        if routed.kind in {CommandKind.START, CommandKind.NEW_GAME}:
            if game.status is not GameStatus.ACTIVE:
                return await self._start(owner_key, utterance, request, next_state, preferences)
            return ConversationReply(
                Speech.of("Начать новую партию и закончить текущую? Скажите «да» или «нет»."),
                replace(
                    self._with_game(next_state, game),
                    pending_action=PendingAction(CommandKind.NEW_GAME, utterance[:255]),
                ),
            )
        if routed.kind is CommandKind.REPEAT_SLOW:
            if state.last_reply is None:
                return ConversationReply(Speech.of("Пока нечего повторять."), self._with_game(next_state, game))
            return ConversationReply(self._slow_repeat(state.last_reply), self._with_game(next_state, game))
        if routed.kind is CommandKind.REPEAT_HEARD:
            heard = routed.heard or "пока ничего"
            return ConversationReply(Speech.of(f"Я услышал: {heard}."), self._with_game(next_state, game))
        if routed.kind is CommandKind.LEVEL_QUERY:
            level = game.engine.skill_level
            hint = _hint(preferences, "Чтобы изменить уровень, скажите: «уровень пять».")
            return ConversationReply(
                Speech.of(
                    f"Сейчас уровень {level}. Шкала — от нуля до двадцати: чем больше число, тем сильнее я играю.{hint}"
                ),
                self._with_game(next_state, game),
            )
        if routed.kind is CommandKind.GAME_FACT:
            fact = answer_game_fact(utterance, board, game.player_color.to_chess())
            if fact is not None:
                return ConversationReply(fact.speech, self._with_game(next_state, game))
        if routed.kind is CommandKind.POSITION_QUERY:
            answer = answer_position_query(utterance, board, state.position_page)
            return ConversationReply(
                answer.speech,
                replace(self._with_game(next_state, game), position_page=answer.page),
            )
        if game.pending_engine_turn is not None and routed.normalized.has_move_tokens:
            result = await self._games.continue_game(owner_key, game.id, request)
            reply = self._turn_reply(owner_key, result, next_state, preferences)
            return replace(
                reply,
                speech=Speech.of(reply.speech.text + " Теперь повторите новый ход."),
            )
        if routed.kind is CommandKind.CLARIFY:
            pending = routed.clarification or state.clarification
            return ConversationReply(
                self._clarification_speech(pending),
                replace(self._with_game(next_state, game), clarification=pending),
            )
        if routed.kind is CommandKind.ILLEGAL_MOVE:
            text = routed.explanation.text if routed.explanation is not None else "Так пойти нельзя."
            return ConversationReply(Speech.of(text), self._with_game(next_state, game))

        if routed.kind is CommandKind.RESIGN:
            return ConversationReply(
                Speech.of("Вы действительно сдаетесь? Скажите «да» или «нет»."),
                replace(
                    self._with_game(next_state, game),
                    pending_action=PendingAction(CommandKind.RESIGN, utterance[:255]),
                ),
            )
        if routed.kind is CommandKind.CLAIM_DRAW:
            result = await self._games.claim_draw(owner_key, game.id, request)
            return self._turn_reply(owner_key, result, next_state, preferences)
        if routed.kind is CommandKind.UNDO:
            return await self._undo(owner_key, game, request, next_state, routed.undo_count)
        if routed.kind is CommandKind.CONTINUE:
            result = await self._games.continue_game(owner_key, game.id, request)
            return self._turn_reply(owner_key, result, next_state, preferences)
        if routed.kind is CommandKind.MOVE and routed.move is not None:
            result = await self._games.play_move(owner_key, game.id, routed.move, request)
            reply = self._turn_reply(owner_key, result, next_state, preferences, echo_player_move=True)
            return self._with_training_warning(owner_key, reply)

        return ConversationReply(
            Speech.of("Не понял команду." + _hint(preferences, "Скажите ход или попросите помощь.")),
            self._with_game(next_state, game),
        )

    def _with_puzzle_card(
        self,
        owner_key: str,
        reply: ConversationReply,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        """Draw the position the attempt now stands on; a closed attempt draws nothing."""
        current = self._puzzles.find_open(owner_key)
        if current is None:
            return reply
        board = current.board()
        solver = PlayerColor.WHITE if board.turn is chess.WHITE else PlayerColor.BLACK
        return replace(
            reply,
            card=compose_position_card(
                board,
                preferences.orientation_for(solver),
                current.last_move,
                "Задача",
            ),
        )

    def _puzzle_reply(
        self,
        owner_key: str,
        open_puzzle: OpenPuzzle,
        utterance: str,
        request: RequestContext,
        prior: ConversationState,
        state: ConversationState,
    ) -> ConversationReply | None:
        """Answer whatever the puzzle owns; `None` lets the game have the utterance."""
        board = open_puzzle.board()
        routed = route(
            utterance,
            board,
            pending=prior.clarification,
            confidence_threshold=self._settings.voice_move_confidence_threshold,
        )
        if routed.kind in _LEAVES_PUZZLE:
            # Asking for a game ends the puzzle rather than keeping both open.
            self._puzzles.abandon(owner_key, open_puzzle)
            return None
        if routed.kind is CommandKind.PUZZLE and routed.puzzle is not None:
            answered = self._puzzles.answer(owner_key, routed.puzzle, request, open_puzzle)
            return ConversationReply(
                answered.speech,
                state,
                sound=answered.sound,
            )
        if routed.kind is CommandKind.DONT_KNOW:
            answered = self._puzzles.answer(
                owner_key,
                PuzzleRequest(PuzzleQuestion.SOLUTION),
                request,
                open_puzzle,
            )
            return ConversationReply(
                answered.speech,
                state,
                sound=answered.sound,
            )
        if (
            routed.kind is CommandKind.TRAINING
            and routed.training is not None
            and routed.training.question is TrainingQuestion.HINT
        ):
            return ConversationReply(self._puzzles.hint(owner_key, open_puzzle, request).speech, state)
        if routed.kind is CommandKind.MOVE and routed.move is not None:
            played = self._puzzles.play(owner_key, open_puzzle, routed.move, request)
            return ConversationReply(played.speech, state, sound=played.sound)
        if routed.kind is CommandKind.ILLEGAL_MOVE:
            text = routed.explanation.text if routed.explanation is not None else "Так пойти нельзя."
            return ConversationReply(Speech.of(text), state)
        if routed.kind is CommandKind.CLARIFY:
            pending = routed.clarification or prior.clarification
            return ConversationReply(self._clarification_speech(pending), replace(state, clarification=pending))
        if routed.kind is CommandKind.POSITION_QUERY:
            answer = answer_position_query(utterance, board, prior.position_page)
            return ConversationReply(answer.speech, replace(state, position_page=answer.page))
        if routed.kind is CommandKind.SCREEN and routed.screen is not None:
            if routed.screen.wish is ScreenWish.TAP:
                return ConversationReply(Speech.of(_SCREEN_TAP_ANSWER), state)
            read = answer_position_query("какая позиция", board, 0)
            return ConversationReply(
                Speech.of(f"{_SCREEN_BIGGER_ANSWER} {read.speech.text}"),
                replace(state, position_page=read.page),
            )
        if routed.kind is CommandKind.UNKNOWN:
            return ConversationReply(
                Speech.of("Не понял. Назовите ход, скажите «подскажи» или «покажи решение»."),
                state,
            )
        return None

    async def _review_reply(
        self,
        owner_key: str,
        game: GameState | None,
        request: ReviewRequest,
        utterance: str,
        state: ConversationState,
    ) -> ConversationReply:
        """Answer a question about a finished game; the game itself stays as it is."""
        reviewed = self._reviewable(owner_key, game)
        if reviewed is None:
            playing = game is not None and game.status is GameStatus.ACTIVE
            unfinished = "Сейчас партия еще идет. Доиграйте ее, потом скажите «разбери партию»." if playing else ""
            return ConversationReply(
                Speech.of(unfinished or "Законченной партии еще нет, разбирать нечего. Скажите «новая игра»."),
                self._with_game(state, game) if game is not None else state,
            )
        if request.question is ReviewQuestion.REPLAY_POSITION:
            return ConversationReply(
                self._review.branch_prompt(),
                replace(
                    self._with_game(state, reviewed),
                    pending_action=PendingAction(CommandKind.REVIEW, utterance[:255], review=request),
                    reviewing=True,
                ),
            )
        speech = await self._review.answer(owner_key, reviewed, request)
        return ConversationReply(
            speech,
            replace(
                self._with_game(state, reviewed),
                reviewing=request.question is not ReviewQuestion.EXIT,
            ),
            # The export is already the spoken answer's text; the card only makes
            # it easier to copy off a screen.
            card=compose_pgn_card(speech.text) if request.question is ReviewQuestion.PGN else None,
        )

    def _reviewable(self, owner_key: str, game: GameState | None) -> GameState | None:
        """The finished game a review question is about, if there is one."""
        if game is not None and game.status is not GameStatus.ACTIVE:
            return game
        with session_scope(self._session_factory) as session:
            opened = ReviewRepository(session).find_latest(owner_key)
        if opened is not None:
            reviewed = self._games.load_game(owner_key, opened.game_id)
            if reviewed.status is not GameStatus.ACTIVE:
                return reviewed
        return self._games.find_latest_finished_game(owner_key)

    async def _start(
        self,
        owner_key: str,
        utterance: str,
        request: RequestContext,
        state: ConversationState,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        unsolved = self._puzzles.find_open(owner_key)
        if unsolved is not None:
            self._puzzles.abandon(owner_key, unsolved)
        # The normaliser's text, not the raw one: «чёрными» only spells «черн» once
        # the ё is folded, and a missed colour silently starts the wrong game.
        spoken = normalize(utterance).text
        player_color = PlayerColor.BLACK if _BLACK.search(spoken) else PlayerColor.WHITE
        named_level = parse_level_value(spoken)
        level = named_level if named_level is not None else self._settings.engine_skill_level
        result = await self._games.start_game(
            owner_key,
            request,
            player_color=player_color,
            engine=EngineSettings(
                skill_level=level,
                move_time_ms=round(self._settings.engine_move_time_seconds * 1000),
            ),
            # Only a genuinely new game may take the mode from the preferences.
            mode=preferences.default_mode,
        )
        side = "черными" if player_color is PlayerColor.BLACK else "белыми"
        reply = self._turn_reply(owner_key, result, state, preferences)
        if request.is_new_session and not utterance.strip():
            return replace(
                reply,
                speech=_new_session_welcome(
                    Speech.of(
                        f"Новая партия уже началась: вы играете {side}, уровень {level}. "
                        f"Назовите ход, например «пешка е два е четыре». {reply.speech.text}"
                    )
                ),
                sound=_opening_sound(reply),
            )
        return replace(
            reply,
            speech=Speech.of(f"Новая партия. Вы играете {side}, уровень {level}. {reply.speech.text}"),
            sound=_opening_sound(reply),
        )

    async def _rematch(
        self,
        owner_key: str,
        base: GameState,
        request: RequestContext,
        rematch: RematchRequest,
        state: ConversationState,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        """Start the next game from the colour and level of the previous one."""
        unsolved = self._puzzles.find_open(owner_key)
        if unsolved is not None:
            self._puzzles.abandon(owner_key, unsolved)
        player_color = _rematch_color(base.player_color, rematch.color)
        level = base.engine.skill_level
        if rematch.harder:
            level = min(MAX_SKILL_LEVEL, level + REMATCH_LEVEL_STEP)
        result = await self._games.start_game(
            owner_key,
            request,
            player_color=player_color,
            engine=EngineSettings(
                skill_level=level,
                move_time_ms=round(self._settings.engine_move_time_seconds * 1000),
            ),
        )
        side = "черными" if player_color is PlayerColor.BLACK else "белыми"
        reply = self._turn_reply(owner_key, result, state, preferences)
        return replace(
            reply,
            speech=Speech.of(f"Реванш. Вы играете {side}, уровень {level}. {reply.speech.text}"),
            sound=_opening_sound(reply),
        )

    async def _undo(
        self,
        owner_key: str,
        game: GameState,
        request: RequestContext,
        state: ConversationState,
        count: int,
    ) -> ConversationReply:
        result = await self._games.undo_turn(owner_key, game.id, request, count)
        undone = int(result.detail or "1") if result.status is TurnStatus.OK else 0
        speech = (
            Speech.of(f"{_undo_confirmation(undone)} Ваш ход.")
            if result.status is TurnStatus.OK
            else compose_turn(result)
        )
        return ConversationReply(speech, self._state_from_turn(state, result), result)

    async def _switch_color(
        self,
        owner_key: str,
        game: GameState,
        request: RequestContext,
        requested: PlayerColor,
        state: ConversationState,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        """Deal the untouched game again from the other side, keeping level and mode."""
        result = await self._games.start_game(
            owner_key,
            request,
            player_color=requested,
            engine=game.engine,
            mode=game.mode,
        )
        side = "черными" if requested is PlayerColor.BLACK else "белыми"
        reply = self._turn_reply(owner_key, result, state, preferences)
        return replace(
            reply,
            speech=Speech.of(f"Хорошо, вы играете {side}. {reply.speech.text}"),
            sound=_opening_sound(reply),
        )

    def _turn_reply(
        self,
        owner_key: str,
        result: TurnResult,
        state: ConversationState,
        preferences: PlayerPreferences,
        echo_player_move: bool = False,
    ) -> ConversationReply:
        loaded = self._load(owner_key, result.game_id)
        # The game as this turn left it. The stored one may have moved on since —
        # a delivery retried late describes its own turn, not the current position.
        turn_state = replace(loaded, moves=result.moves) if loaded is not None else None
        settled = turn_state.board() if turn_state is not None else None
        board_before_engine: chess.Board | None = None
        if result.engine_move is not None and settled is not None:
            board_before_engine = settled.copy()
            if board_before_engine.move_stack and board_before_engine.peek().uci() == result.engine_move:
                board_before_engine.pop()
        board_after_player = _board_after_player(result, settled)
        commentary = self._commentary(owner_key, result, turn_state, preferences)
        echoed = _player_move_echo(result, board_after_player, preferences.notation_style) if echo_player_move else None
        # The composer drops a remark the engine's move already made; a check the
        # echo announces is the player's own and needs the same guard.
        if echoed is not None and echoed.endswith(" Шах.") and commentary is not None and "шах" in commentary.lower():
            commentary = None
        speech = compose_turn(result, board_before_engine, preferences.notation_style, commentary)
        if (
            preferences.detail_level is DetailLevel.DETAILED
            and _player_to_move(result)
            and "ваш ход" not in speech.text.lower()
        ):
            speech = Speech.of(f"{speech.text} Сейчас ваш ход.")
        if echoed is not None:
            speech = Speech.of(f"{PLAYER_MOVE_PREFIX}{echoed} {speech.text}")
        return ConversationReply(
            speech,
            self._state_from_turn(state, result),
            result,
            player_sound=_player_sound(result, board_after_player),
            engine_sound=_engine_sound(result),
        )

    def _commentary(
        self,
        owner_key: str,
        result: TurnResult,
        state: GameState | None,
        preferences: PlayerPreferences,
    ) -> str | None:
        """Remark on the move just played, if it was worth remarking on.

        A finished game says nothing extra: the outcome already carries the news.
        """
        if state is None or result.status is not TurnStatus.OK or result.outcome is not None:
            return None
        if result.player_move is None and result.engine_move is None:
            return None
        comment = comment_on(
            state.initial_fen,
            state.moves,
            state.player_color,
            preferences.detail_level,
            self._training.centipawn_losses(owner_key, state),
        )
        return comment.text if comment is not None else None

    async def _enable_trainer_and_answer(
        self,
        owner_key: str,
        game: GameState,
        asked: TrainingRequest,
        request: RequestContext,
        state: ConversationState,
    ) -> ConversationReply:
        """Turn the trainer on and answer the question that asked for it, in one reply."""
        enabling = await self._training.answer(owner_key, game, TrainingRequest(TrainingQuestion.ENABLE), request)
        enabled = self._reload(owner_key, game)
        if enabled.mode is not GameMode.TRAINING:
            return ConversationReply(enabling, self._with_game(state, enabled))
        answer = await self._training.answer(owner_key, enabled, asked, request)
        return ConversationReply(
            Speech.of(f"Включаю режим тренера. {answer.text}"),
            self._with_game(state, self._reload(owner_key, enabled)),
        )

    @staticmethod
    def _level_change_reply(change: LevelChange, state: ConversationState) -> ConversationReply:
        if change.status is LevelChangeStatus.APPLIED:
            speech = f"Установил уровень {change.level}. Партия продолжается. Ваш ход."
        elif change.status is LevelChangeStatus.UNCHANGED:
            speech = f"Уровень {change.level} уже установлен. Назовите другой уровень — от нуля до двадцати."
        elif change.status is LevelChangeStatus.ENGINE_TO_MOVE:
            speech = "Сначала я сделаю свой ход. Скажите «продолжаем», потом повторите уровень."
        else:
            speech = _LEVEL_GAME_OVER
        return ConversationReply(
            Speech.of(speech),
            replace(state, game_id=change.game_id, revision=change.revision),
        )

    def _replayed_turn_reply(
        self,
        owner_key: str,
        utterance: str,
        result: TurnResult,
        state: ConversationState,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        replay_state = replace(state, last_heard=utterance.strip() or state.last_heard)
        reply = self._turn_reply(owner_key, result, replay_state, preferences, echo_player_move=True)
        if _named_player_move(result) is not None:
            return reply
        if state.game_id != result.game_id:
            side = "черными" if result.player_color is PlayerColor.BLACK else "белыми"
            game = self._load(owner_key, result.game_id)
            level = game.engine.skill_level if game is not None else self._settings.engine_skill_level
            return replace(
                reply,
                speech=Speech.of(f"Новая партия. Вы играете {side}, уровень {level}. {reply.speech.text}"),
                sound=_opening_sound(reply),
            )
        return reply

    @staticmethod
    def _resume_prompt(game: GameState, timezone_name: str | None) -> Speech:
        if game.last_player_move_at is None:
            opening = "У вас есть незаконченная партия, в которой вы еще не сделали ход."
        else:
            played = _date_phrase(game.last_player_move_at, timezone_name)
            opening = f"У вас есть незаконченная партия, в которую вы последний раз играли {played}."

        board = game.board()
        if not board.move_stack:
            history = "Ходов еще не было."
        elif len(board.move_stack) == 1:
            history = f"Последний ход: {describe_recent_moves(board, 1).text}"
        else:
            history = f"Последние два хода: {describe_recent_moves(board, 2).text}"
        return Speech.of(f"{opening} {history} Продолжить?")

    def _reload(self, owner_key: str, game: GameState) -> GameState:
        """Re-read a game a coaching answer may have re-moded or hinted."""
        return self._load(owner_key, game.id) or game

    def _with_training_warning(self, owner_key: str, reply: ConversationReply) -> ConversationReply:
        """Warn about a costly training move; the move itself always stands."""
        game = self._load(owner_key, reply.state.game_id)
        warning = self._training.warning(owner_key, game) if game is not None else None
        if warning is None:
            return reply
        return replace(reply, speech=Speech.of(f"{reply.speech.text} {warning.text}"))

    def _load(self, owner_key: str, game_id: str | None) -> GameState | None:
        if game_id is None:
            return None
        try:
            return self._games.load_game(owner_key, game_id)
        except LookupError:
            return None

    def _help_reply(
        self,
        answer: HelpAnswer,
        state: ConversationState,
        game: GameState | None,
    ) -> ConversationReply:
        """Help only reads: the game, its revision and any pending turn stay as they are."""
        help_state = replace(state, help=answer.state)
        return ConversationReply(
            answer.speech,
            self._with_game(help_state, game) if game is not None else help_state,
            card=compose_help_card() if answer.state is not None else None,
        )

    @staticmethod
    def _with_game(state: ConversationState, game: GameState) -> ConversationState:
        return replace(state, game_id=game.id, revision=game.revision)

    @staticmethod
    def _state_from_turn(state: ConversationState, result: TurnResult) -> ConversationState:
        return replace(state, game_id=result.game_id, revision=result.revision, clarification=None)

    @staticmethod
    def _clarification_speech(pending: PendingClarification | None) -> Speech:
        if pending is None:
            return Speech.of("Уточните ход.")
        if not pending.candidates:
            normalized = normalize(pending.heard)
            if contains_multiple_moves(normalized):
                return Speech.of("Я услышал несколько ходов. Назовите только ваш текущий ход.")
            recognized = recognize(normalized.signature)
            if recognized.piece is not None and recognized.destination is None:
                return Speech.of(f"Куда пойти {_spoken_piece(recognized.piece)}? Назовите поле.")
            if recognized.destination is not None:
                return Speech.of(f"Какой фигурой вы хотите пойти на {recognized.destination}?")
            return Speech.of("Назовите ваш ход: фигуру и поле назначения.")
        if len(pending.candidates) == 1:
            return Speech.of(f"Я услышал «{pending.heard}». Подтвердите ход {_display_uci(pending.candidates[0])}.")
        choices = ", или ".join(_display_uci(candidate) for candidate in pending.candidates[:6])
        return Speech.of(f"Ход неоднозначен. Уточните: {choices}.")

    @staticmethod
    def _slow_repeat(text: str) -> Speech:
        words = [word for word in text.split() if word not in {"—", "-"}]
        return Speech(text=f"Повторяю: {text}", tts="Повторяю медленно. " + ", ".join(words))

    def _record(
        self,
        owner_key: str,
        routed: RoutedCommand,
        board: chess.Board | None,
        request: RequestContext,
        pending_action: PendingAction | None,
        interpreted_kind: CommandKind | None = None,
    ) -> None:
        effective_kind = interpreted_kind or routed.kind
        resolution = routed.resolution if effective_kind is routed.kind else None
        confirmation = pending_action is not None and (
            confirmation_answer(routed.normalized.text, pending_action.kind) is not None
            or (pending_action.kind is CommandKind.CONTINUE and routed.kind is CommandKind.CONTINUE)
        )
        empty = not routed.normalized.text
        command_kind = "empty" if empty else "confirmation" if confirmation else effective_kind.value
        routing_outcome = "empty" if empty else "confirmation" if confirmation else _routing_outcome(effective_kind)
        resolved_request_key = usage_request_key(request.skill_id, request.session_id, request.message_id)
        with session_scope(self._session_factory) as session:
            UsageRepository(session).record_request(
                owner_key,
                request.skill_id,
                request.session_id,
                request.message_id,
                request.traffic_source,
                datetime.now(UTC).replace(tzinfo=None),
                release_id=self._settings.release_id,
                command_kind=command_kind,
                resolution_status=resolution.status.value if resolution is not None and not confirmation else None,
                routing_outcome=routing_outcome,
            )
            if routed.normalized.text:
                TranscriptRepository(session, self._settings.asr_transcript_text_limit).record(
                    owner_key,
                    routed.normalized.text,
                    "confirmation" if confirmation else resolution.status if resolution is not None else effective_kind,
                    confidence=resolution.confidence if resolution is not None else 0.0,
                    candidate_count=len(resolution.candidates) if resolution is not None else 0,
                    legal_move_count=board.legal_moves.count() if board is not None else 0,
                    request_key=resolved_request_key,
                )


# Commands that are about a game, not about the puzzle that happens to be open.
_LEAVES_PUZZLE = frozenset(
    {
        CommandKind.CONTINUE,
        CommandKind.UNDO,
        CommandKind.CLAIM_DRAW,
    }
)

_REVIEW_NEXT = re.compile(r"^(дальше|далее|еще|ещё|следующ\w*)$")
_REVIEW_PREVIOUS = re.compile(r"^(назад|обратно|предыдущ\w*)$")
_REVIEW_RESTART = re.compile(r"^(сначала|с начала|заново|в начало|начало)$")


def _review_step(text: str) -> int | None:
    """How far a navigation word moves the dictation, or `None` if it is not one."""
    if _REVIEW_NEXT.match(text):
        return 1
    if _REVIEW_PREVIOUS.match(text):
        return -1
    if _REVIEW_RESTART.match(text):
        return 0
    return None


def _hint(preferences: PlayerPreferences, text: str) -> str:
    """Advisory tails are dropped for a player who asked for short answers.

    Only advice is ever dropped: what the position is and what happened in it is
    said at every detail level.
    """
    return "" if preferences.detail_level is DetailLevel.BRIEF else f" {text}"


def _routing_outcome(kind: CommandKind) -> str:
    if kind is CommandKind.UNKNOWN:
        return "unknown"
    if kind is CommandKind.ILLEGAL_MOVE:
        return "illegal_move"
    if kind is CommandKind.CLARIFY:
        return "clarification"
    return "handled"


def _conversational_reply(
    kind: CommandKind,
    text: str,
    game: GameState | None,
    solving_puzzle: bool,
    pending_action: PendingAction | None,
) -> Speech:
    """Keep short conversational turns useful without changing chess state."""
    if kind is CommandKind.PAUSE:
        saved = " Партия сохранена." if game is not None and game.status is GameStatus.ACTIVE else ""
        return Speech.of(f"Хорошо, подожду.{saved}")
    if kind is CommandKind.AMBIGUOUS_TURN:
        return Speech.of("Что вы хотите: сделать ход, услышать последний ход или открыть помощь?")
    if kind is CommandKind.ORIENTATION_QUERY:
        return Speech.of("Как показать доску: за белых или за черных?")
    if kind is CommandKind.NAVIGATE_BACK:
        return Speech.of("Куда вернуться: к партии, выйти из задач или закрыть справку?")

    if pending_action is not None:
        expectation = "Скажите «да» или «нет»."
    elif solving_puzzle:
        expectation = "Назовите ход, попросите подсказку или покажите решение."
    elif game is not None and game.status is GameStatus.ACTIVE:
        expectation = "Ваш ход."
    elif game is not None:
        expectation = "Можно разобрать партию или начать новую."
    else:
        expectation = "Скажите «новая игра», чтобы начать."

    if kind is CommandKind.ATTENTION:
        return Speech.of(f"Слушаю. {expectation}")
    if kind is CommandKind.SOCIAL:
        if text in {"как тебя зовут", "кто ты"}:
            return Speech.of(f"Я Юра, шахматный помощник Алисы. {expectation}")
        if text in {"ты тут", "ты здесь"}:
            return Speech.of(f"Да, я здесь. {expectation}")
        return Speech.of(f"Здравствуйте! {expectation}")
    return Speech.of(f"Хорошо. {expectation}")


def _display_uci(uci: str) -> str:
    """Separate UCI squares so Alice spells each one instead of reading a word."""
    promotion = f" {uci[4]}" if len(uci) == 5 else ""
    return f"{uci[:2]} {uci[2:4]}{promotion}"


def _spoken_piece(piece: str) -> str:
    """Instrumental: «пойти пешкой» agrees for every piece, «пойти пешка» for none."""
    return {"P": "пешкой", "N": "конем", "B": "слоном", "R": "ладьей", "Q": "ферзем", "K": "королем"}[piece]


def _undo_confirmation(count: int) -> str:
    if count == 1:
        return "Один полный ход отменен."
    if 2 <= count <= 4:
        return f"{count} полных хода отменено."
    return f"{count} полных ходов отменено."


def _player_to_move(result: TurnResult) -> bool:
    return result.status is TurnStatus.OK and chess.Board(result.fen).turn == result.player_color.to_chess()


def _opening_sound(reply: ConversationReply) -> SoundEvent | None:
    """A fresh game announces itself only when no move in the answer already sounds."""
    if reply.player_sound is not None or reply.engine_sound is not None:
        return reply.sound
    return SoundEvent.START


def _named_player_move(result: TurnResult) -> str | None:
    """The player's move when this answer is the one that should say it.

    A turn owed since an earlier request carries a move that request already
    named; saying and sounding it again would replay a ply the player has heard.
    """
    return None if result.settles_owed_reply else result.player_move


def _player_move_echo(
    result: TurnResult,
    board_after_player: chess.Board | None,
    notation: NotationStyle,
) -> str | None:
    """Name the player's own move the way the engine's move is named.

    The outcome sentence already announces a mate or a stalemate, so the echo
    drops them; a check the player gave is theirs alone and stays.
    """
    named = _named_player_move(result)
    if named is None:
        return None
    if board_after_player is None:
        return f"{_display_uci(named)}."
    before = board_after_player.copy(stack=True)
    move = before.pop()
    return describe_move(before, move, notation).text.removesuffix(" Мат.").removesuffix(" Пат.")


def _board_after_player(result: TurnResult, settled: chess.Board | None) -> chess.Board | None:
    """The position the player's move left, rewound from that turn's own history.

    A turn settled by a concurrent request reports the position after the engine
    has already answered; the player's own move must not be read off that.
    """
    if result.player_move is None or settled is None:
        return None
    board = settled.copy()
    while board.move_stack and board.peek().uci() != result.player_move:
        board.pop()
    return board if board.move_stack else None


def _player_sound(result: TurnResult, board_after_player: chess.Board | None) -> SoundEvent | None:
    """Sound the player's own move, and their win when the mate is theirs."""
    if result.player_move is None:
        return None
    if (
        result.outcome is not None
        and result.outcome.end is GameEnd.CHECKMATE
        and result.outcome.winner is result.player_color
    ):
        return SoundEvent.SUCCESS
    if board_after_player is None:
        return SoundEvent.MOVE
    return SoundEvent.CHECK if board_after_player.is_check() else SoundEvent.MOVE


def _engine_sound(result: TurnResult) -> SoundEvent | None:
    """Sound the engine's move; an owed reply names no move and stays silent."""
    if result.engine_move is None:
        return None
    if result.outcome is not None and result.outcome.end is GameEnd.CHECKMATE:
        return SoundEvent.CHECKMATE
    return SoundEvent.CHECK if chess.Board(result.fen).is_check() else SoundEvent.MOVE


def _color_switch_question(game: GameState, requested: PlayerColor) -> str:
    """Ask before a started game is thrown away; the colour itself is fixed at the deal."""
    now = "черными" if game.player_color is PlayerColor.BLACK else "белыми"
    wanted = "черных" if requested is PlayerColor.BLACK else "белых"
    return (
        f"Сейчас вы играете {now}, а цвет меняется только в новой партии. "
        f"Закончить эту и начать новую за {wanted}? Скажите «да» или «нет»."
    )


def _rematch_color(previous: PlayerColor, requested: RematchColor) -> PlayerColor:
    if requested is RematchColor.WHITE:
        return PlayerColor.WHITE
    if requested is RematchColor.BLACK:
        return PlayerColor.BLACK
    if requested is RematchColor.SWAP:
        return PlayerColor.BLACK if previous is PlayerColor.WHITE else PlayerColor.WHITE
    return previous


_DETAIL_CONFIRMATIONS: dict[DetailLevel, str] = {
    DetailLevel.BRIEF: "Буду отвечать кратко.",
    DetailLevel.NORMAL: "Буду отвечать как обычно.",
    DetailLevel.DETAILED: "Буду отвечать подробнее.",
}

_NOTATION_CONFIRMATIONS: dict[NotationStyle, str] = {
    NotationStyle.FULL: "Буду называть обе клетки хода.",
    NotationStyle.SHORT: "Буду называть только клетку, куда идет фигура.",
}

# The skill cannot speed Alice up or slow her down; it only adds or drops its own pauses.
_PAUSE_CONFIRMATIONS: dict[PauseStyle, str] = {
    PauseStyle.EXTENDED: "Добавлю паузы между фразами. Скорость речи Алисы я не меняю.",
    PauseStyle.NORMAL: "Убрал добавленные паузы. Скорость речи Алисы я не меняю.",
}

_ORIENTATION_CONFIRMATIONS: dict[BoardOrientation, str] = {
    BoardOrientation.WHITE: "Доска на экране будет всегда белыми снизу.",
    BoardOrientation.BLACK: "Доска на экране будет всегда черными снизу.",
    BoardOrientation.PLAYER: "Доска на экране будет с вашей стороны.",
}

_SOUND_CONFIRMATIONS = {
    True: "Звуки включены.",
    False: "Звуки выключены.",
}


def _preference_confirmation(change: PreferenceChange) -> str:
    """Confirm only the settings this command named."""
    parts = [
        _DETAIL_CONFIRMATIONS[change.detail_level] if change.detail_level is not None else "",
        _PAUSE_CONFIRMATIONS[change.pause_style] if change.pause_style is not None else "",
        _NOTATION_CONFIRMATIONS[change.notation_style] if change.notation_style is not None else "",
        _ORIENTATION_CONFIRMATIONS[change.board_orientation] if change.board_orientation is not None else "",
        _SOUND_CONFIRMATIONS[change.sounds_enabled] if change.sounds_enabled is not None else "",
    ]
    return " ".join(part for part in parts if part) or "Настройка не изменилась."


def _needs_trainer_consent(game: GameState, asked: TrainingRequest) -> bool:
    """Whether a coaching question must be paid for by turning the trainer on first."""
    if asked.question in {TrainingQuestion.ENABLE, TrainingQuestion.DISABLE}:
        return False
    return game.status is GameStatus.ACTIVE and game.mode is not GameMode.TRAINING


def _help_mode(game: GameState | None, solving_puzzle: bool = False) -> HelpMode:
    if solving_puzzle:
        return HelpMode.PUZZLE
    if game is None:
        return HelpMode.NO_GAME
    if game.status is not GameStatus.ACTIVE:
        return HelpMode.GAME_OVER
    return HelpMode.TRAINING if game.mode is GameMode.TRAINING else HelpMode.GAME


def _date_phrase(value: datetime, timezone_name: str | None) -> str:
    try:
        timezone = ZoneInfo(timezone_name) if timezone_name else UTC
    except ZoneInfoNotFoundError:
        timezone = UTC
    instant = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    local_date = instant.astimezone(timezone).date()
    today = datetime.now(timezone).date()
    if local_date == today:
        return "сегодня"
    if local_date == today - timedelta(days=1):
        return "вчера"
    return f"{local_date.day} {_MONTHS[local_date.month]}"
