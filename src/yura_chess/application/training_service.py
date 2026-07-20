"""Coaching that reads the position and never plays it.

Every answer here is derived from a read-only analysis of a *copy* of the board:
the UCI history, the revision and any pending engine turn are the game's alone.
The only rows this service writes are the two it owns — the coaching mode and
hint stage of the game, and the analysis checkpoints — and both are written by
value, so a re-delivered request stores what the first delivery stored.

Coaching is available only in `GameMode.TRAINING`. In an honest game a coaching
question is answered with the offer to switch on the trainer and nothing else:
naming the engine's move there would silently turn the game into a training one.

The engine is bound by the short analysis deadline, so a busy or slow search is
a spoken apology rather than a failed turn. A missing checkpoint is likewise
normal: the position can always be valued again.
"""

from __future__ import annotations

from typing import Protocol

import chess
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import TrainingQuestion, TrainingRequest
from yura_chess.application.game_service import RequestContext
from yura_chess.domain.analysis import (
    MISTAKE_CENTIPAWNS,
    AnalysisCheckpoint,
    AnalysisEngineSettings,
    MoveCandidate,
    PositionAnalysis,
    Score,
    centipawn_loss,
    position_hash,
)
from yura_chess.domain.game import MAX_HINT_STAGE, GameMode, GameState, GameStatus, PlayerColor
from yura_chess.engine.stockfish import EngineSearchTimeoutError, EngineUnavailableError
from yura_chess.presentation.move_speech import (
    PIECE_NAMES,
    PIECE_NAMES_ACCUSATIVE,
    Speech,
    describe_move,
)
from yura_chess.settings import Settings
from yura_chess.storage.analysis_repository import AnalysisRepository
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.voice.illegal_move import explain
from yura_chess.voice.move_resolver import resolve
from yura_chess.voice.normalizer import normalize
from yura_chess.voice.types import ResolutionStatus

# A threat is only worth naming when the opponent's free move would win this
# much; below it every quiet move would be announced as a threat.
THREAT_CENTIPAWNS = 150

# Verbal evaluation bands, in centipawns from the asking player's side.
_DECISIVE = 300
_CLEAR = 150
_SLIGHT = 50


class PositionSearch(Protocol):
    """The read-only engine capability; `StockfishPool` satisfies it."""

    async def analyse(
        self,
        board: chess.Board,
        search_time: float | None = None,
        candidates: int | None = None,
    ) -> PositionAnalysis: ...


class TrainingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: PositionSearch,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._settings = settings

    async def answer(
        self,
        owner_key: str,
        game: GameState,
        request: TrainingRequest,
        context: RequestContext,
    ) -> Speech:
        """Answer one coaching question without touching the game's own state."""
        if request.question is TrainingQuestion.ENABLE:
            return self._set_mode(owner_key, game, GameMode.TRAINING)
        if request.question is TrainingQuestion.DISABLE:
            return self._set_mode(owner_key, game, GameMode.GAME)
        if game.mode is not GameMode.TRAINING:
            # The offer, not the answer: an honest game stays honest.
            return Speech.of(
                "Сейчас идет обычная партия без подсказок. "
                "Скажите «включи режим тренера», и я буду оценивать позицию и подсказывать."
            )
        if request.question is TrainingQuestion.KEEP_MOVE:
            return Speech.of("Хорошо, ваш ход остается в партии.")
        if request.question is TrainingQuestion.WHERE_WRONG:
            return self._where_wrong(owner_key, game)
        if request.question is TrainingQuestion.HINT:
            return await self._hint(owner_key, game, context)
        if request.question is TrainingQuestion.PREVIEW:
            return await self._preview(game, request.move_text or "")
        if request.question is TrainingQuestion.WHY_MOVE:
            return self._why_last_move(game)
        if request.question is TrainingQuestion.THREAT:
            return await self._threat(game)
        if request.question is TrainingQuestion.CANDIDATES:
            return await self._candidates(game)
        return await self._evaluation(game, numeric=request.question is TrainingQuestion.EVALUATION_NUMBER)

    async def observe_player_move(self, owner_key: str, state: GameState, ply: int, move_uci: str) -> None:
        """Value an accepted training move before the engine answers it.

        Runs with no transaction open. Both the analysis and the checkpoint are
        optional: a busy engine leaves the move unvalued rather than unplayed.
        """
        if state.mode is not GameMode.TRAINING:
            return
        board = chess.Board(state.initial_fen)
        for uci in state.moves[:ply]:
            board.push(chess.Move.from_uci(uci))
        mover = PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK
        before = await self._analyse(board)
        if before is None:
            return
        score_before = before.score_for(mover)
        after = await self._score_after(board, move_uci, before, mover)
        if score_before is None or after is None:
            return
        checkpoint = AnalysisCheckpoint(
            game_id=state.id,
            owner_key=owner_key,
            ply=ply,
            position_hash=position_hash(board.fen()),
            score_before=score_before,
            score_after=after,
            centipawn_loss=centipawn_loss(score_before, after),
            engine=AnalysisEngineSettings(
                depth=before.depth,
                search_time_ms=round(self._settings.engine_analysis_time_seconds * 1000),
                skill_level=state.engine.skill_level,
            ),
        )
        with session_scope(self._session_factory) as session:
            AnalysisRepository(session).upsert(checkpoint)

    def last_mistake(self, owner_key: str, game: GameState) -> AnalysisCheckpoint | None:
        """The most recent still-played move that cost at least a mistake."""
        with session_scope(self._session_factory) as session:
            checkpoints = AnalysisRepository(session).list_for_game(game.id, owner_key)
        played = len(game.moves)
        significant = [
            checkpoint
            for checkpoint in checkpoints
            # A taken-back move keeps its row but is no longer part of the game.
            if checkpoint.ply < played and checkpoint.centipawn_loss >= MISTAKE_CENTIPAWNS
        ]
        return significant[-1] if significant else None

    def centipawn_losses(self, owner_key: str, game: GameState) -> dict[int, int]:
        """What every valued move of this game cost its player, by ply.

        Read-only and complete for a training game, so a comment derived from it
        is the same after a reload as it was when the move was played.
        """
        if game.mode is not GameMode.TRAINING:
            return {}
        with session_scope(self._session_factory) as session:
            checkpoints = AnalysisRepository(session).list_for_game(game.id, owner_key)
        played = len(game.moves)
        # A taken-back move keeps its row but is no longer part of the game.
        return {point.ply: point.centipawn_loss for point in checkpoints if point.ply < played}

    def warning(self, owner_key: str, game: GameState) -> Speech | None:
        """Warn about the player's last move, once the turn is already complete."""
        if game.mode is not GameMode.TRAINING:
            return None
        checkpoint = self.last_mistake(owner_key, game)
        if checkpoint is None or checkpoint.ply < len(game.moves) - 2:
            return None
        board = chess.Board(game.initial_fen)
        for uci in game.moves[: checkpoint.ply]:
            board.push(chess.Move.from_uci(uci))
        return Speech.of(
            f"Внимание: ваш ход {board.fullmove_number} потерял {_pawns(checkpoint.centipawn_loss)}. "
            "Скажите «оставить мой ход» или «вернуть ход»."
        )

    def _set_mode(self, owner_key: str, game: GameState, mode: GameMode) -> Speech:
        if game.mode is mode:
            return Speech.of(
                "Режим тренера уже включен." if mode is GameMode.TRAINING else "Мы и так играем без подсказок."
            )
        if game.status is not GameStatus.ACTIVE:
            return Speech.of("Партия уже закончена. Режим можно выбрать в новой партии.")
        with session_scope(self._session_factory) as session:
            GameRepository(session).set_mode(game.id, owner_key, game.revision, mode)
        if mode is GameMode.TRAINING:
            return Speech.of(
                "Включаю режим тренера. Я буду оценивать позицию, отвечать на вопросы, "
                "давать подсказки и предупреждать об ошибках. Это уже не честная партия."
            )
        return Speech.of("Выключаю режим тренера. Дальше играем без подсказок.")

    async def _evaluation(self, game: GameState, numeric: bool) -> Speech:
        analysis = await self._analyse(game.board())
        if analysis is None:
            return _busy()
        score = analysis.score_for(game.player_color)
        if score is None:
            return Speech.of("Оценивать нечего: ходов в этой позиции нет.")
        if numeric:
            return Speech.of(f"Оценка позиции: {_numeric(score)}.")
        return Speech.of(f"{_verbal(score)}. Скажите «назови оценку числом», если нужна цифра.")

    def _why_last_move(self, game: GameState) -> Speech:
        """The purpose of the engine's last move, read from the position itself."""
        board = game.board()
        if not board.move_stack or board.turn != game.player_color.to_chess():
            return Speech.of("Я еще не ходила в этой позиции.")
        move = board.peek()
        board.pop()
        return Speech.of(f"Мой ход {_move_phrase(board, move.uci())}. {_purpose(board, move)}")

    async def _threat(self, game: GameState) -> Speech:
        board = game.board()
        if board.is_check():
            return Speech.of("Прямо сейчас вам шах — это и есть угроза.")
        if board.is_game_over():
            return Speech.of("Партия закончена, угрожать больше нечем.")
        before = await self._analyse(board)
        if before is None:
            return _busy()
        # The threat is what the opponent would play if the move were theirs.
        free = board.copy(stack=False)
        free.push(chess.Move.null())
        after = await self._analyse(free)
        if after is None:
            return _busy()
        current = before.score_for(game.player_color)
        threatened = after.score_for(game.player_color)
        best = after.best
        if current is None or threatened is None or best is None:
            return Speech.of("Ясной угрозы я не вижу.")
        if current.as_centipawns() - threatened.as_centipawns() < THREAT_CENTIPAWNS:
            return Speech.of("Ясной угрозы сейчас нет.")
        return Speech.of(f"Я угрожаю сыграть {_move_phrase(free, best.move)}.")

    async def _candidates(self, game: GameState) -> Speech:
        board = game.board()
        if board.turn != game.player_color.to_chess():
            return Speech.of("Сейчас не ваш ход, выбирать пока нечего.")
        analysis = await self._analyse(board)
        if analysis is None:
            return _busy()
        best = analysis.candidates[:3]
        if not best:
            return Speech.of("Ходов в этой позиции нет.")
        moves = ", ".join(_move_phrase(board, candidate.move) for candidate in best)
        return Speech.of(f"Хорошие ходы, от лучшего: {moves}.")

    async def _preview(self, game: GameState, move_text: str) -> Speech:
        """Value a suggested move on a copy; the game never sees it."""
        board = game.board()
        normalized = normalize(move_text)
        if not normalized.has_move_tokens:
            return Speech.of("Назовите ход, который разобрать, например «что будет, если я сыграю конь эф три».")
        resolution = resolve(normalized, board)
        if resolution.status is ResolutionStatus.UNMATCHED:
            return Speech.of(explain(resolution.recognized, board).text)
        if resolution.status is not ResolutionStatus.RESOLVED or resolution.move is None:
            choices = ", или ".join(resolution.candidates[:6])
            return Speech.of(f"Не поняла, какой ход разобрать. Уточните: {choices}.")
        move = chess.Move.from_uci(resolution.move)
        preview = board.copy(stack=False)
        preview.push(move)
        analysis = await self._analyse(preview)
        if analysis is None:
            return _busy()
        score = analysis.score_for(game.player_color)
        if score is None:
            return Speech.of(f"После {_move_phrase(board, move.uci())} партия сразу заканчивается.")
        return Speech.of(f"После {_move_phrase(board, move.uci())} {_verbal(score).lower()}. Ход я не делаю.")

    async def _hint(self, owner_key: str, game: GameState, context: RequestContext) -> Speech:
        """Four escalating hints; a repeated request advances the stage once."""
        board = game.board()
        if board.turn != game.player_color.to_chess():
            return Speech.of("Сейчас мой ход, подсказывать пока нечего.")
        analysis = await self._analyse(board)
        if analysis is None or analysis.best is None:
            return _busy() if analysis is None else Speech.of("Ходов в этой позиции нет.")
        stage = self._advance_hint(owner_key, game, context)
        move = chess.Move.from_uci(analysis.best.move)
        return Speech.of(_hint_text(board, move, stage))

    def _advance_hint(self, owner_key: str, game: GameState, context: RequestContext) -> int:
        """Move one stage up, but only for the first delivery of this request.

        The target stage depends on the stage already stored, so an absolute
        write is not idempotent by itself; claiming the request makes it so. The
        claim carries no game id: a hint is not a turn, and nothing may later
        resume it as one.
        """
        with session_scope(self._session_factory) as session:
            repository = GameRepository(session)
            _, created = repository.record_request(
                context.skill_id,
                context.session_id,
                context.message_id,
                context.fingerprint,
                owner_key,
            )
            current = repository.load(game.id, owner_key)
            if not created:
                return current.hint_stage
            stage = min(current.hint_stage + 1, MAX_HINT_STAGE)
            repository.set_hint_stage(game.id, owner_key, current.revision, stage)
            return stage

    def _where_wrong(self, owner_key: str, game: GameState) -> Speech:
        checkpoint = self.last_mistake(owner_key, game)
        if checkpoint is None:
            return Speech.of("Существенных ошибок я у вас не вижу.")
        board = chess.Board(game.initial_fen)
        for uci in game.moves[: checkpoint.ply]:
            board.push(chess.Move.from_uci(uci))
        move = chess.Move.from_uci(game.moves[checkpoint.ply])
        return Speech.of(
            f"Ход {board.fullmove_number}, {_move_phrase(board, move.uci())}: "
            f"он стоил {_pawns(checkpoint.centipawn_loss)}."
        )

    async def _score_after(
        self,
        board: chess.Board,
        move_uci: str,
        before: PositionAnalysis,
        mover: PlayerColor,
    ) -> Score | None:
        """Reuse the candidate for the played move; search again only if it is missing."""
        played = _candidate_for(before, move_uci)
        if played is not None:
            return played.score if mover is before.side_to_move else played.score.inverted()
        after = board.copy(stack=False)
        after.push(chess.Move.from_uci(move_uci))
        analysis = await self._analyse(after)
        return analysis.score_for(mover) if analysis is not None else None

    async def _analyse(self, board: chess.Board) -> PositionAnalysis | None:
        """`None` means the engine could not answer in time, never a bad position."""
        try:
            return await self._engine.analyse(board)
        except (EngineUnavailableError, EngineSearchTimeoutError):
            return None


def _candidate_for(analysis: PositionAnalysis, move_uci: str) -> MoveCandidate | None:
    for candidate in analysis.candidates:
        if candidate.move == move_uci:
            return candidate
    return None


def _busy() -> Speech:
    return Speech.of("Не успела посчитать позицию. Спросите еще раз чуть позже. Партия не изменилась.")


def _verbal(score: Score) -> str:
    """Name the evaluation as a category, from the asking player's side."""
    if score.mate_in is not None:
        return "У вас форсированный мат" if score.mate_in > 0 else "У меня форсированный мат"
    value = score.centipawns or 0
    if value >= _DECISIVE:
        return "У вас решающий перевес"
    if value >= _CLEAR:
        return "У вас заметный перевес"
    if value >= _SLIGHT:
        return "У вас небольшой перевес"
    if value > -_SLIGHT:
        return "Позиция примерно равная"
    if value > -_CLEAR:
        return "У меня небольшой перевес"
    if value > -_DECISIVE:
        return "У меня заметный перевес"
    return "У меня решающий перевес"


def _numeric(score: Score) -> str:
    if score.mate_in is not None:
        side = "в вашу пользу" if score.mate_in > 0 else "в мою пользу"
        return f"мат в {abs(score.mate_in)} {side}"
    value = (score.centipawns or 0) / 100
    sign, side = ("плюс", "в вашу пользу") if value >= 0 else ("минус", "в мою пользу")
    return f"{sign} {abs(value):.1f} пешки {side}"


def _pawns(centipawns: int) -> str:
    return f"{centipawns / 100:.1f} пешки"


def _purpose(board: chess.Board, move: chess.Move) -> str:
    """One concrete goal of `move`, read from the position before it."""
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return "Это мат."
    if after.is_check():
        return "Он объявляет шах."
    captured = board.piece_at(move.to_square)
    if captured is not None:
        return f"Он забирает {PIECE_NAMES_ACCUSATIVE[captured.piece_type]}."
    if move.promotion is not None:
        return "Пешка превращается в фигуру."
    if board.is_castling(move):
        return "Я увожу короля в безопасность."
    attacked = _attacked_piece(after, move.to_square)
    if attacked is not None:
        return f"Он нападает на {PIECE_NAMES_ACCUSATIVE[attacked]}."
    return f"Так {_piece_name(board, move.from_square)} стоит активнее."


def _idea(board: chess.Board, move: chess.Move) -> str:
    """The first hint: what kind of move to look for, not which one."""
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return "здесь есть мат"
    if after.is_check():
        return "здесь есть ход с шахом"
    if board.is_capture(move):
        return "здесь есть взятие"
    if move.promotion is not None:
        return "пешка может пройти в ферзи"
    if _attacked_piece(after, move.to_square) is not None:
        return "здесь есть нападение на фигуру"
    return "лучший ход тихий: улучшите положение фигуры"


def _attacked_piece(after: chess.Board, square: chess.Square) -> chess.PieceType | None:
    """The first non-pawn enemy piece the mover attacks from `square`."""
    mover = after.piece_at(square)
    if mover is None:
        return None
    for target in after.attacks(square):
        piece = after.piece_at(target)
        if piece is not None and piece.color != mover.color and piece.piece_type != chess.PAWN:
            return piece.piece_type
    return None


def _move_phrase(board: chess.Board, move_uci: str) -> str:
    """Name a move as a phrase, without the full stop that ends a sentence."""
    return describe_move(board, chess.Move.from_uci(move_uci)).text.rstrip(".")


def _piece_name(board: chess.Board, square: chess.Square) -> str:
    piece = board.piece_at(square)
    return PIECE_NAMES[piece.piece_type] if piece is not None else "фигура"


def _hint_text(board: chess.Board, move: chess.Move, stage: int) -> str:
    again = " Скажите «дай подсказку» еще раз."
    if stage <= 1:
        return f"Подсказка: {_idea(board, move)}.{again}"
    if stage == 2:
        return f"Подсказка: ходить надо так — {_piece_name(board, move.from_square)}.{again}"
    if stage == 3:
        return f"Подсказка: поле назначения — {chess.square_name(move.to_square)}.{again}"
    return f"Полная подсказка: {_move_phrase(board, move.uci())}."
