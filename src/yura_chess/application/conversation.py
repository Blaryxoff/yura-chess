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
    CommandKind,
    PendingClarification,
    PreferenceChange,
    RematchColor,
    RematchRequest,
    ReviewQuestion,
    ReviewRequest,
    RoutedCommand,
    TrainingQuestion,
    confirmation_answer,
    route,
)
from yura_chess.application.game_service import GameService, MoveSearch, RequestContext
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
from yura_chess.domain.results import TurnResult, TurnStatus
from yura_chess.presentation import help_speech
from yura_chess.presentation.commentary import comment_on
from yura_chess.presentation.game_facts import answer_game_fact
from yura_chess.presentation.help_speech import HelpAnswer, HelpMode, HelpState
from yura_chess.presentation.move_speech import Speech, add_pauses
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

MAX_SKILL_LEVEL = 20
# One rematch step up is two of the twenty engine levels: less is not audible.
REMATCH_LEVEL_STEP = 2

_BLACK = re.compile(r"\bчерн")
_LEVEL_WORDS = {
    "ноль": 0,
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
}
_LEVEL = re.compile(rf"\b(?:уровень|сложность)\s*(?P<value>\d{{1,2}}|{'|'.join(_LEVEL_WORDS)})\b")
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
                ConversationReply(replayed_puzzle.speech, replay_state),
                preferences,
            )
        else:
            replayed = await self._games.resume_request(owner_key, request)
            reply = (
                self._replayed_turn_reply(owner_key, utterance, replayed, prior_state, preferences)
                if replayed is not None
                else await self._handle(owner_key, utterance, request, prior_state, preferences)
            )
        if route(utterance).kind is not CommandKind.REPEAT_SLOW:
            # Stored before the pauses are added, so a repeat reads words rather
            # than speech markup.
            reply = replace(reply, state=replace(reply.state, last_reply=reply.speech.spoken()[:512]))
        # A settings command answers with the preferences it just stored.
        effective = reply.preferences or preferences
        return replace(reply, speech=add_pauses(reply.speech, effective.pause_style), preferences=effective)

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
                return self._with_puzzle_card(
                    owner_key,
                    ConversationReply(
                        self._puzzles.resume_prompt(unsolved),
                        replace(state, pending_action=PendingAction(CommandKind.PUZZLE, "")),
                    ),
                    preferences,
                )
            candidate = game if game is not None and game.status is GameStatus.ACTIVE else None
            candidate = candidate or self._games.find_latest_active_game(owner_key)
            if candidate is not None:
                prompt_state = replace(
                    self._with_game(state, candidate),
                    pending_action=PendingAction(CommandKind.CONTINUE, ""),
                )
                return ConversationReply(self._resume_prompt(candidate, request.timezone), prompt_state)

        board = game.board() if game is not None else None
        routed = route(
            utterance,
            board,
            pending=state.clarification,
            last_heard=state.last_heard,
            confidence_threshold=self._settings.voice_move_confidence_threshold,
        )
        self._record(owner_key, routed, board)

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

        if state.pending_action is not None:
            confirmation = confirmation_answer(utterance)
            if confirmation is None:
                return ConversationReply(Speech.of("Скажите «да» или «нет»."), next_state)
            confirmed = state.pending_action
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
                return ConversationReply(Speech.of("Хорошо, отменяю."), cancelled_state)
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
                    )
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

        open_puzzle = self._puzzles.find_open(owner_key)
        mode = _help_mode(game, open_puzzle is not None)
        # Open help owns «дальше», «назад» and «сначала»: otherwise they would be
        # read as board pagination, as a new game, or as a step of the puzzle.
        if state.help is not None:
            navigated = help_speech.navigate(utterance, state.help, mode) or help_speech.bare_topic(utterance, mode)
            if navigated is not None:
                return self._help_reply(navigated, next_state, game)

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
            step = _review_step(routed.normalized.text)
            if reviewed is not None and step is not None:
                return ConversationReply(
                    self._review.dictate(owner_key, reviewed, step),
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
                ),
                preferences,
            )

        if routed.kind is CommandKind.TRAINING and routed.training is not None:
            if game is None:
                return ConversationReply(
                    Speech.of("Партии еще нет, тренировать нечего. Скажите «новая игра»."),
                    next_state,
                )
            speech = await self._training.answer(owner_key, game, routed.training, request)
            return ConversationReply(speech, self._with_game(next_state, self._reload(owner_key, game)))

        if game is None:
            if routed.kind is CommandKind.GAME_FACT:
                return ConversationReply(
                    Speech.of("Партии еще нет, поэтому рассказать о ней нечего. Скажите «новая игра»."),
                    next_state,
                )
            if routed.kind is CommandKind.LEVEL_QUERY:
                level = self._settings.engine_skill_level
                hint = _hint(preferences, "Чтобы выбрать другой, скажите «новая игра уровень десять».")
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
            return ConversationReply(Speech.of(f"Я услышала: {heard}."), self._with_game(next_state, game))
        if routed.kind is CommandKind.LEVEL_QUERY:
            level = game.engine.skill_level
            hint = _hint(preferences, "Чтобы изменить его, скажите «новая игра уровень десять».")
            return ConversationReply(
                Speech.of(f"Сейчас установлен уровень сложности {level} из 20.{hint}"),
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
        if routed.kind is CommandKind.CANCEL_CLARIFY:
            return ConversationReply(
                Speech.of("Хорошо, ход не делаю. Назовите другой ход."),
                self._with_game(next_state, game),
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
            result = await self._games.undo_turn(owner_key, game.id, request)
            speech = (
                Speech.of("Последний полный ход отменен. Ваш ход.")
                if result.status is TurnStatus.OK
                else compose_turn(result)
            )
            return ConversationReply(speech, self._state_from_turn(next_state, result), result)
        if routed.kind is CommandKind.CONTINUE:
            result = await self._games.continue_game(owner_key, game.id, request)
            return self._turn_reply(owner_key, result, next_state, preferences)
        if routed.kind is CommandKind.MOVE and routed.move is not None:
            result = await self._games.play_move(owner_key, game.id, routed.move, request)
            reply = self._turn_reply(owner_key, result, next_state, preferences)
            if result.player_move is not None:
                reply = replace(reply, speech=Speech.of(f"Ваш ход: {result.player_move}. {reply.speech.text}"))
            return self._with_training_warning(owner_key, reply)

        return ConversationReply(
            Speech.of("Не поняла команду." + _hint(preferences, "Скажите ход или попросите помощь.")),
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
            return ConversationReply(
                self._puzzles.answer(owner_key, routed.puzzle, request, open_puzzle).speech,
                state,
            )
        if (
            routed.kind is CommandKind.TRAINING
            and routed.training is not None
            and routed.training.question is TrainingQuestion.HINT
        ):
            return ConversationReply(self._puzzles.hint(owner_key, open_puzzle, request).speech, state)
        if routed.kind is CommandKind.MOVE and routed.move is not None:
            return ConversationReply(self._puzzles.play(owner_key, open_puzzle, routed.move, request).speech, state)
        if routed.kind is CommandKind.ILLEGAL_MOVE:
            text = routed.explanation.text if routed.explanation is not None else "Так пойти нельзя."
            return ConversationReply(Speech.of(text), state)
        if routed.kind is CommandKind.CLARIFY:
            pending = routed.clarification or prior.clarification
            return ConversationReply(self._clarification_speech(pending), replace(state, clarification=pending))
        if routed.kind is CommandKind.CANCEL_CLARIFY:
            return ConversationReply(Speech.of("Хорошо, ход не делаю. Назовите другой ход."), state)
        if routed.kind is CommandKind.POSITION_QUERY:
            answer = answer_position_query(utterance, board, prior.position_page)
            return ConversationReply(answer.speech, replace(state, position_page=answer.page))
        if routed.kind is CommandKind.UNKNOWN:
            return ConversationReply(
                Speech.of("Не поняла. Назовите ход, скажите «подскажи» или «покажи решение»."),
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
            return ConversationReply(
                Speech.of("Законченной партии еще нет, разбирать нечего. Скажите «новая игра»."),
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
        player_color = PlayerColor.BLACK if _BLACK.search(utterance.lower()) else PlayerColor.WHITE
        level_match = _LEVEL.search(utterance.lower())
        level_value = level_match.group("value") if level_match else None
        level = (
            max(0, min(int(level_value), 20))
            if level_value is not None and level_value.isdigit()
            else _LEVEL_WORDS.get(level_value or "", self._settings.engine_skill_level)
        )
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
                speech=Speech.of(
                    f"Это навык «Шахматы с Юрой». Здесь вы играете в шахматы голосом против компьютера. "
                    f"Новая партия уже началась: вы играете {side}, уровень {level}. "
                    f"Назовите ход, например «пешка е два е четыре», или скажите «помощь». {reply.speech.text}"
                ),
            )
        return replace(
            reply,
            speech=Speech.of(f"Новая партия. Вы играете {side}, уровень {level}. {reply.speech.text}"),
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
        )

    def _turn_reply(
        self,
        owner_key: str,
        result: TurnResult,
        state: ConversationState,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        final_state = self._load(owner_key, result.game_id)
        board_before_engine: chess.Board | None = None
        if result.engine_move is not None and final_state is not None:
            board_before_engine = final_state.board()
            if final_state.moves and final_state.moves[-1] == result.engine_move:
                board_before_engine.pop()
        speech = compose_turn(
            result,
            board_before_engine,
            preferences.notation_style,
            self._commentary(owner_key, result, final_state, preferences),
        )
        if (
            preferences.detail_level is DetailLevel.DETAILED
            and _player_to_move(result)
            and "ваш ход" not in speech.text.lower()
        ):
            speech = Speech.of(f"{speech.text} Сейчас ваш ход.")
        return ConversationReply(
            speech,
            self._state_from_turn(state, result),
            result,
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

    def _replayed_turn_reply(
        self,
        owner_key: str,
        utterance: str,
        result: TurnResult,
        state: ConversationState,
        preferences: PlayerPreferences,
    ) -> ConversationReply:
        replay_state = replace(state, last_heard=utterance.strip() or state.last_heard)
        reply = self._turn_reply(owner_key, result, replay_state, preferences)
        if result.player_move is not None:
            return replace(reply, speech=Speech.of(f"Ваш ход: {result.player_move}. {reply.speech.text}"))
        if state.game_id != result.game_id:
            side = "черными" if result.player_color is PlayerColor.BLACK else "белыми"
            game = self._load(owner_key, result.game_id)
            level = game.engine.skill_level if game is not None else self._settings.engine_skill_level
            return replace(
                reply,
                speech=Speech.of(f"Новая партия. Вы играете {side}, уровень {level}. {reply.speech.text}"),
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
        if len(pending.candidates) == 1:
            return Speech.of(f"Я услышала «{pending.heard}». Подтвердите ход {pending.candidates[0]}.")
        choices = ", или ".join(pending.candidates[:6])
        return Speech.of(f"Ход неоднозначен. Уточните: {choices}.")

    @staticmethod
    def _slow_repeat(text: str) -> Speech:
        words = [word for word in text.split() if word not in {"—", "-"}]
        return Speech(text=f"Повторяю: {text}", tts="Повторяю медленно. " + ", ".join(words))

    def _record(self, owner_key: str, routed: RoutedCommand, board: chess.Board | None) -> None:
        if not routed.normalized.text:
            return
        resolution = routed.resolution
        with session_scope(self._session_factory) as session:
            TranscriptRepository(session, self._settings.asr_transcript_text_limit).record(
                owner_key,
                routed.normalized.text,
                resolution.status if resolution is not None else routed.kind,
                confidence=resolution.confidence if resolution is not None else 0.0,
                candidate_count=len(resolution.candidates) if resolution is not None else 0,
                legal_move_count=board.legal_moves.count() if board is not None else 0,
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


def _player_to_move(result: TurnResult) -> bool:
    return result.status is TurnStatus.OK and chess.Board(result.fen).turn == result.player_color.to_chess()


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
    DetailLevel.NORMAL: "Возвращаю обычную подробность ответов.",
    DetailLevel.DETAILED: "Буду отвечать подробнее.",
}

_NOTATION_CONFIRMATIONS: dict[NotationStyle, str] = {
    NotationStyle.FULL: "Буду называть обе клетки хода.",
    NotationStyle.SHORT: "Буду называть только клетку, куда идет фигура.",
}

# The skill cannot speed Alice up or slow her down; it only adds or drops its own pauses.
_PAUSE_CONFIRMATIONS: dict[PauseStyle, str] = {
    PauseStyle.EXTENDED: "Добавлю паузы между фразами. Скорость речи Алисы я не меняю.",
    PauseStyle.NORMAL: "Убрала добавленные паузы. Скорость речи Алисы я не меняю.",
}

_ORIENTATION_CONFIRMATIONS: dict[BoardOrientation, str] = {
    BoardOrientation.WHITE: "Доска на экране будет всегда белыми снизу.",
    BoardOrientation.BLACK: "Доска на экране будет всегда черными снизу.",
    BoardOrientation.PLAYER: "Доска на экране будет с вашей стороны.",
}


def _preference_confirmation(change: PreferenceChange) -> str:
    """Confirm only the settings this command named."""
    parts = [
        _DETAIL_CONFIRMATIONS[change.detail_level] if change.detail_level is not None else "",
        _PAUSE_CONFIRMATIONS[change.pause_style] if change.pause_style is not None else "",
        _NOTATION_CONFIRMATIONS[change.notation_style] if change.notation_style is not None else "",
        _ORIENTATION_CONFIRMATIONS[change.board_orientation] if change.board_orientation is not None else "",
    ]
    return " ".join(part for part in parts if part) or "Настройка не изменилась."


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
