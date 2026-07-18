"""Turn a `TurnResult` into the two strings a voice reply consists of.

Every answer is complete as speech alone: nothing here depends on a screen, a
card or an image, so a voice-only device gets the full state of the game. The
`tts` string is produced only when the pronunciation differs from the display
text — otherwise Alice speaks the text itself.
"""

from __future__ import annotations

import chess

from yura_chess.domain.game import PlayerColor
from yura_chess.domain.results import GameEnd, GameOutcome, TurnResult, TurnStatus
from yura_chess.presentation.move_speech import Speech, describe_move, describe_played_move

_STATUS_TEXTS: dict[TurnStatus, str] = {
    TurnStatus.ENGINE_UNAVAILABLE: "Ваш ход записан, я ещё думаю над ответом. Скажите «продолжаем».",
    TurnStatus.NOT_PLAYER_TURN: "Сейчас не ваш ход.",
    TurnStatus.GAME_ALREADY_FINISHED: "Партия уже окончена. Скажите «новая игра».",
    TurnStatus.DRAW_NOT_CLAIMABLE: "Ничью сейчас потребовать нельзя.",
    TurnStatus.UNDO_REJECTED: "Сейчас отменить ход не могу.",
    TurnStatus.ILLEGAL_MOVE: "Так пойти нельзя.",
}

_DRAW_TEXTS: dict[GameEnd, str] = {
    GameEnd.STALEMATE: "Пат. Ничья.",
    GameEnd.INSUFFICIENT_MATERIAL: "Недостаточно материала для мата. Ничья.",
    GameEnd.SEVENTY_FIVE_MOVES: "Семьдесят пять ходов без взятий и ходов пешкой. Ничья.",
    GameEnd.FIVEFOLD_REPETITION: "Позиция повторилась пять раз. Ничья.",
    GameEnd.FIFTY_MOVES: "Правило пятидесяти ходов. Ничья.",
    GameEnd.THREEFOLD_REPETITION: "Троекратное повторение позиции. Ничья.",
}


def compose_turn(result: TurnResult, board_before: chess.Board | None = None) -> Speech:
    """Say what the turn did; `board_before` is the position the engine moved in."""
    parts = [text for text in (_move_text(result, board_before), _outcome_text(result)) if text]
    if not parts:
        return Speech.of(_STATUS_TEXTS.get(result.status, "Ваш ход."))
    return Speech.of(" ".join(parts))


def _move_text(result: TurnResult, board_before: chess.Board | None) -> str:
    if result.status is TurnStatus.ENGINE_UNAVAILABLE:
        return _STATUS_TEXTS[TurnStatus.ENGINE_UNAVAILABLE]
    if result.engine_move is None:
        return ""
    move = chess.Move.from_uci(result.engine_move)
    if board_before is not None:
        return "Мой ход. " + describe_move(board_before, move).text
    # Without the previous position the moving piece is still readable off the
    # destination square; only what it captured is lost.
    return "Мой ход. " + describe_played_move(chess.Board(result.fen), move).text


def _outcome_text(result: TurnResult) -> str:
    outcome = result.outcome
    if outcome is None:
        return ""
    if outcome.end is GameEnd.CHECKMATE:
        return f"Мат. {_winner_text(result, outcome)}"
    if outcome.end is GameEnd.RESIGNATION:
        return "Вы сдались. Партия окончена."
    return _DRAW_TEXTS[outcome.end]


def _winner_text(result: TurnResult, outcome: GameOutcome) -> str:
    if outcome.winner is None:
        return "Партия окончена."
    side = "Белые" if outcome.winner is PlayerColor.WHITE else "Черные"
    verdict = "Вы выиграли." if outcome.winner is result.player_color else "Вы проиграли."
    return f"{side} выиграли. {verdict}"
