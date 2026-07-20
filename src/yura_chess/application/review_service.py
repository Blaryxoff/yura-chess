"""Go through a finished game without changing a single thing in it.

The reviewed game is over: its UCI history, revision and status are read here and
never written. The only rows this service owns are the analysis checkpoints it
fills in and the review cursor that remembers where the reading stopped.

Valuing a whole game is far too much work for one voice turn, so each request
values at most `PLIES_PER_REQUEST` player moves, always with no transaction open,
and says honestly how far it got. The checkpoints themselves are the memo: a
later request simply skips the plies that already have one, which is what makes
an interrupted review resumable and a re-delivered request harmless.

A checkpoint is written by value, so re-analysing a move stores the same verdict
it stored the first time.
"""

from __future__ import annotations

import chess
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.application.command_router import ReviewQuestion, ReviewRequest
from yura_chess.application.training_service import PositionSearch
from yura_chess.domain.analysis import (
    BLUNDER_CENTIPAWNS,
    INACCURACY_CENTIPAWNS,
    MISTAKE_CENTIPAWNS,
    AnalysisCheckpoint,
    AnalysisEngineSettings,
    MoveCandidate,
    MoveQuality,
    PositionAnalysis,
    Score,
    centipawn_loss,
    position_hash,
)
from yura_chess.domain.game import EngineSettings, GameMode, GameState, GameStatus, PlayerColor
from yura_chess.domain.results import GameEnd, GameOutcome, automatic_outcome, claimable_draw
from yura_chess.domain.review import GameReview, ReviewSection
from yura_chess.engine.stockfish import EngineSearchTimeoutError, EngineUnavailableError
from yura_chess.presentation import pgn
from yura_chess.presentation.move_speech import Speech, describe_move
from yura_chess.settings import Settings
from yura_chess.storage.analysis_repository import AnalysisRepository
from yura_chess.storage.database import session_scope
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.review_repository import ReviewRepository, ReviewRevisionConflictError

# How many player moves one voice turn may value; the rest waits for «продолжить
# разбор». The Alice budget, not the engine, sets this limit.
PLIES_PER_REQUEST = 6

_CONTINUE = " Скажите «продолжить разбор», чтобы досчитать остальное."
_PGN_PREVIEW_LIMIT = 850


class ReviewService:
    """Answer questions about a finished game; the game itself stays untouched."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: PositionSearch,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._settings = settings

    async def answer(self, owner_key: str, game: GameState, request: ReviewRequest) -> Speech:
        """Answer one review question, valuing at most one bounded batch of moves."""
        if request.question is ReviewQuestion.EXIT:
            return self.close(owner_key, game)
        if request.question is ReviewQuestion.PGN:
            return self._pgn(owner_key, game)
        if request.question is ReviewQuestion.MOVES:
            return self.dictate(owner_key, game, step=0)
        losses, complete = await self._valuations(owner_key, game)
        if request.question is ReviewQuestion.CONTINUE and complete:
            # Only a finished analysis may hand «продолжить разбор» back to the
            # dictation; otherwise the partial tail would promise a count that
            # can never be reached.
            resumed = self._resume_moves(owner_key, game)
            if resumed is not None:
                return resumed
        if request.question is ReviewQuestion.MISTAKE_COUNT:
            return Speech.of(_counts_text(losses, complete) + _partial_tail(game, losses, complete))
        if request.question is ReviewQuestion.TURNING_POINT:
            return await self._turning_point_speech(game, losses, complete)
        if request.question is ReviewQuestion.MAIN_MISTAKE:
            return await self._main_mistake_speech(game, losses, complete)
        return await self._summary(game, losses, complete)

    def dictate(self, owner_key: str, game: GameState, step: int) -> Speech:
        """Read the moves page by page; `step` moves the stored cursor."""
        review = self._open(owner_key, game)
        return self._dictate_page(owner_key, game, review, 0 if step == 0 else review.page + step)

    def _dictate_page(self, owner_key: str, game: GameState, review: GameReview, page: int) -> Speech:
        pages = pgn.move_pages(game)
        page = max(0, min(page, len(pages) - 1))
        self._set_cursor(owner_key, game, review, ReviewSection.MOVES, page)
        lines = pages[page]
        if not lines:
            return Speech.of("В этой партии ходов не было.")
        tail = (
            " Скажите «дальше», чтобы продолжить."
            if page + 1 < len(pages)
            else " Это конец партии. Скажите «выйти из разбора»."
        )
        return Speech.of(" ".join(lines) + tail)

    def close(self, owner_key: str, game: GameState) -> Speech:
        """Forget the cursor; the game and its checkpoints stay as they are."""
        with session_scope(self._session_factory) as session:
            ReviewRepository(session).finish(game.id, owner_key)
        return Speech.of("Закрываю разбор. Скажите «новая игра» или «реванш».")

    @staticmethod
    def branch_prompt() -> Speech:
        """Ask before starting a training branch; nothing is created yet."""
        return Speech.of("Сыграть переломную позицию заново в режиме тренера? Скажите «да» или «нет».")

    async def start_branch(self, owner_key: str, game: GameState) -> tuple[str | None, Speech]:
        """Open a training game from the turning-point position.

        The branch is a new game with its own id: the finished one keeps its
        history, revision and status exactly as they were.
        """
        losses, complete = await self._valuations(owner_key, game)
        if not complete:
            return None, Speech.of("Разбор еще не закончен. Скажите «продолжить разбор», затем повторите запрос.")
        turning = _turning_point(losses)
        if turning is None:
            return None, Speech.of("Переломного момента я не нашла, переигрывать нечего.")
        board = _board_before(game, turning.ply)
        with session_scope(self._session_factory) as session:
            branch = GameRepository(session).create_game(
                owner_key,
                game.player_color,
                EngineSettings(
                    skill_level=game.engine.skill_level,
                    move_time_ms=game.engine.move_time_ms,
                ),
                initial_fen=board.fen(),
                mode=GameMode.TRAINING,
            )
        number = board.fullmove_number
        return branch.id, Speech.of(
            f"Играем с хода {number} в режиме тренера. Прежняя партия осталась без изменений. Ваш ход."
        )

    async def _summary(
        self,
        game: GameState,
        losses: dict[int, AnalysisCheckpoint],
        complete: bool,
    ) -> Speech:
        parts = [_result_text(game), _counts_text(losses, complete)]
        turning = _turning_point(losses)
        if turning is not None:
            parts.append(f"Перелом — {_move_reference(game, turning.ply)}.")
            better = await self._better_move(game, turning.ply)
            if better is not None:
                parts.append(f"Практичнее было {better}.")
        parts.append("Скажите «где был перелом», «какая моя главная ошибка» или «продиктуй партию».")
        return Speech.of(" ".join(parts) + _partial_tail(game, losses, complete))

    async def _turning_point_speech(
        self,
        game: GameState,
        losses: dict[int, AnalysisCheckpoint],
        complete: bool,
    ) -> Speech:
        turning = _turning_point(losses)
        if turning is None:
            if not complete:
                return Speech.of(
                    "В уже разобранной части резкого перелома не видно." + _partial_tail(game, losses, complete)
                )
            return Speech.of("Резкого перелома в этой партии не было." + _partial_tail(game, losses, complete))
        better = await self._better_move(game, turning.ply)
        alternative = f" Практичнее было {better}." if better is not None else ""
        return Speech.of(
            f"Перелом — {_move_reference(game, turning.ply)}: он стоил {_pawns(turning.centipawn_loss)}."
            f"{alternative} Скажите «сыграть эту позицию заново», чтобы попробовать иначе."
            + _partial_tail(game, losses, complete)
        )

    async def _main_mistake_speech(
        self,
        game: GameState,
        losses: dict[int, AnalysisCheckpoint],
        complete: bool,
    ) -> Speech:
        worst = _worst(losses, MISTAKE_CENTIPAWNS)
        if worst is None:
            if not complete:
                return Speech.of(
                    "В уже разобранной части существенной ошибки пока не видно." + _partial_tail(game, losses, complete)
                )
            return Speech.of("Существенных ошибок я у вас не вижу." + _partial_tail(game, losses, complete))
        better = await self._better_move(game, worst.ply)
        alternative = f" Практичнее было {better}." if better is not None else ""
        return Speech.of(
            f"Главная ошибка — {_move_reference(game, worst.ply)}: потеря {_pawns(worst.centipawn_loss)}."
            f"{alternative}" + _partial_tail(game, losses, complete)
        )

    def _pgn(self, owner_key: str, game: GameState) -> Speech:
        """The export for the screen; the same moves stay available by voice."""
        review = self._open(owner_key, game)
        self._set_cursor(owner_key, game, review, ReviewSection.MOVES, 0)
        export = pgn.export(game, _outcome(game))
        if len(export) > _PGN_PREVIEW_LIMIT:
            prefix = export[:_PGN_PREVIEW_LIMIT].rsplit(" ", 1)[0]
            return Speech(
                text=(
                    "PGN слишком длинный для одной карточки. Ниже только начало записи:\n"
                    f"{prefix}\n\nЗапись сокращена. Скажите «продиктуй партию», чтобы услышать все ходы."
                ),
                tts="PGN слишком длинный для одной карточки. Скажите «продиктуй партию», чтобы услышать все ходы.",
            )
        return Speech(
            text=export,
            tts="Партия в нотации PGN. Голосом читаю ходы по страницам: скажите «продиктуй партию».",
        )

    def _resume_moves(self, owner_key: str, game: GameState) -> Speech | None:
        """Re-read the page the cursor stopped on, even in a brand new session."""
        with session_scope(self._session_factory) as session:
            review = ReviewRepository(session).find(game.id, owner_key)
        if review is None or review.section is not ReviewSection.MOVES:
            return None
        return self._dictate_page(owner_key, game, review, review.page)

    async def _valuations(
        self,
        owner_key: str,
        game: GameState,
    ) -> tuple[dict[int, AnalysisCheckpoint], bool]:
        """Value the player's moves, at most one batch per request.

        Returns what is known so far and whether every player move is valued.
        """
        with session_scope(self._session_factory) as session:
            stored = AnalysisRepository(session).list_for_game(game.id, owner_key)
        known = {point.ply: point for point in stored if point.ply < len(game.moves)}
        missing = [ply for ply in _player_plies(game) if ply not in known]
        for ply in missing[:PLIES_PER_REQUEST]:
            checkpoint = await self._value_ply(owner_key, game, ply)
            if checkpoint is None:
                # A busy or slow engine leaves the rest for the next request.
                return known, False
            known[checkpoint.ply] = checkpoint
        return known, len(missing) <= PLIES_PER_REQUEST

    async def _value_ply(self, owner_key: str, game: GameState, ply: int) -> AnalysisCheckpoint | None:
        """Value one played move the same way the trainer values a live one."""
        board = _board_before(game, ply)
        mover = PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK
        before = await self._analyse(board)
        if before is None:
            return None
        score_before = before.score_for(mover)
        after = await self._score_after(board, game.moves[ply], before, mover)
        if score_before is None or after is None:
            return None
        checkpoint = AnalysisCheckpoint(
            game_id=game.id,
            owner_key=owner_key,
            ply=ply,
            position_hash=position_hash(board.fen()),
            score_before=score_before,
            score_after=after,
            centipawn_loss=centipawn_loss(score_before, after),
            engine=AnalysisEngineSettings(
                depth=before.depth,
                search_time_ms=round(self._settings.engine_analysis_time_seconds * 1000),
                skill_level=self._settings.engine_analysis_skill_level,
            ),
        )
        with session_scope(self._session_factory) as session:
            return AnalysisRepository(session).upsert(checkpoint)

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

    async def _better_move(self, game: GameState, ply: int) -> str | None:
        """The move the engine would have played instead, named as a phrase."""
        board = _board_before(game, ply)
        analysis = await self._analyse(board)
        if analysis is None or analysis.best is None:
            return None
        if analysis.best.move == game.moves[ply]:
            return None
        return describe_move(board, chess.Move.from_uci(analysis.best.move)).text.rstrip(".")

    async def _analyse(self, board: chess.Board) -> PositionAnalysis | None:
        """`None` means the engine could not answer in time, never a bad position."""
        try:
            return await self._engine.analyse(board)
        except (EngineUnavailableError, EngineSearchTimeoutError):
            return None

    def _open(self, owner_key: str, game: GameState) -> GameReview:
        with session_scope(self._session_factory) as session:
            return ReviewRepository(session).start(game.id, owner_key)

    def _set_cursor(
        self,
        owner_key: str,
        game: GameState,
        review: GameReview,
        section: ReviewSection,
        page: int,
    ) -> None:
        """Store the cursor absolutely, so re-reading the same page is a no-op."""
        if review.section is section and review.page == page:
            return
        for _ in range(2):
            try:
                with session_scope(self._session_factory) as session:
                    repository = ReviewRepository(session)
                    current = repository.find(game.id, owner_key)
                    if current is None:
                        current = repository.start(game.id, owner_key)
                    if current.section is section and current.page == page:
                        return
                    repository.set_cursor(game.id, owner_key, current.revision, section, page=page)
                return
            except ReviewRevisionConflictError:
                continue


def _player_plies(game: GameState) -> tuple[int, ...]:
    player_moves_first = game.player_color.to_chess() == chess.Board(game.initial_fen).turn
    return tuple(ply for ply in range(len(game.moves)) if (ply % 2 == 0) is player_moves_first)


def _board_before(game: GameState, ply: int) -> chess.Board:
    board = chess.Board(game.initial_fen)
    for uci in game.moves[:ply]:
        board.push(chess.Move.from_uci(uci))
    return board


def _candidate_for(analysis: PositionAnalysis, move_uci: str) -> MoveCandidate | None:
    for candidate in analysis.candidates:
        if candidate.move == move_uci:
            return candidate
    return None


def _turning_point(losses: dict[int, AnalysisCheckpoint]) -> AnalysisCheckpoint | None:
    """The move that decided the game, not merely the most expensive one.

    A turning point is the earliest move that gave a still playable position
    away for good; when no single move did that, it is the largest loss the
    thresholds still call a mistake.
    """
    for ply in sorted(losses):
        checkpoint = losses[ply]
        if checkpoint.centipawn_loss < MISTAKE_CENTIPAWNS:
            continue
        if checkpoint.score_before.as_centipawns() > -BLUNDER_CENTIPAWNS >= checkpoint.score_after.as_centipawns():
            return checkpoint
    return _worst(losses, MISTAKE_CENTIPAWNS)


def _worst(losses: dict[int, AnalysisCheckpoint], threshold: int) -> AnalysisCheckpoint | None:
    significant = [point for point in losses.values() if point.centipawn_loss >= threshold]
    if not significant:
        return None
    # The earliest of equally expensive moves: it is the one that started it.
    return max(significant, key=lambda point: (point.centipawn_loss, -point.ply))


def _counts_text(losses: dict[int, AnalysisCheckpoint], complete: bool = True) -> str:
    counted = [point.quality for point in losses.values()]
    blunders = counted.count(MoveQuality.BLUNDER)
    mistakes = counted.count(MoveQuality.MISTAKE)
    inaccuracies = counted.count(MoveQuality.INACCURACY)
    if not (blunders or mistakes or inaccuracies):
        if not complete:
            return "Пока не удалось оценить достаточно ходов, чтобы честно посчитать ошибки."
        return "Существенных ошибок в ваших ходах я не нашла."
    return (
        f"Неточностей {inaccuracies}, ошибок {mistakes}, грубых ошибок {blunders}. "
        f"Считаю по порогам {INACCURACY_CENTIPAWNS}, {MISTAKE_CENTIPAWNS} и {BLUNDER_CENTIPAWNS} сантипешек."
    )


def _move_reference(game: GameState, ply: int) -> str:
    board = _board_before(game, ply)
    described = describe_move(board, chess.Move.from_uci(game.moves[ply])).text.rstrip(".")
    return f"ход {board.fullmove_number}, {described}"


def _partial_tail(game: GameState, losses: dict[int, AnalysisCheckpoint], complete: bool) -> str:
    if complete:
        return ""
    return f" Разобрала {len(losses)} ваших ходов из {len(_player_plies(game))}.{_CONTINUE}"


def _pawns(centipawns: int) -> str:
    return f"{centipawns / 100:.1f} пешки"


def _outcome(game: GameState) -> GameOutcome | None:
    """How the finished game ended, read from its own history and status.

    A draw the player demanded leaves no automatic outcome on the board, so a
    finished game without one is read as the draw that was claimable in it.
    """
    if game.status is GameStatus.RESIGNED:
        winner = PlayerColor.BLACK if game.player_color is PlayerColor.WHITE else PlayerColor.WHITE
        return GameOutcome(GameEnd.RESIGNATION, winner)
    board = game.board()
    outcome = automatic_outcome(board)
    if outcome is not None or game.status is not GameStatus.FINISHED:
        return outcome
    claimed = claimable_draw(board)
    return GameOutcome(claimed) if claimed is not None else None


def _result_text(game: GameState) -> str:
    outcome = _outcome(game)
    if outcome is None:
        return "Партия не доиграна до конца."
    if outcome.end is GameEnd.RESIGNATION:
        return "Вы сдались."
    if outcome.winner is None:
        return "Партия закончилась вничью."
    return "Вы выиграли." if outcome.winner is game.player_color else "Вы проиграли."
