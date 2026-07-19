"""A short remark about the move that was just played — or, usually, silence.

A quiet move gets no comment: only a whitelist of genuinely notable events is
worth interrupting the game for. The whole decision is a pure walk of the
canonical history, so the same game always produces the same remark — a replayed
request, a reloaded session and a fresh process cannot disagree.

Nothing here talks to the engine. In an honest game every category is decided by
the rules alone; the one engine-derived category is fed in by the caller as the
centipawn losses already stored for a training game, so an honest game simply
has no engine commentary to make.

The evaluation category speaks only about swings *in the player's favour*: a
costly move is the training warning's subject, and saying it twice would nag.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

import chess

from yura_chess.domain.analysis import BLUNDER_CENTIPAWNS
from yura_chess.domain.game import PlayerColor
from yura_chess.domain.preferences import DetailLevel
from yura_chess.presentation.move_speech import PIECE_NAMES_ACCUSATIVE
from yura_chess.presentation.opening import GameStage, OpeningName, game_stage, identify_opening

# Two full moves of silence follow any remark, and the same subject is never
# raised twice in a row however far apart the two occurrences are.
COMMENT_COOLDOWN_PLIES = 4

# A swing is worth naming once it is worth a minor piece. It is measured against
# the balance two plies earlier, so a capture answered by an immediate recapture
# nets out and stays silent.
MATERIAL_SWING = 3
_SETTLING_PLIES = 2

_PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

_STAGE_ENTERED: dict[GameStage, str] = {
    GameStage.OPENING: "Партия вернулась в дебют.",
    GameStage.MIDDLEGAME: "Партия перешла в миттельшпиль.",
    GameStage.ENDGAME: "Партия перешла в эндшпиль.",
}


class CommentCategory(StrEnum):
    """The only events a comment is ever made about."""

    PROMOTION = "promotion"
    MATERIAL = "material"
    CHECK = "check"
    EVALUATION = "evaluation"
    STAGE = "stage"
    OPENING = "opening"


@dataclass(frozen=True, slots=True)
class Comment:
    category: CommentCategory
    text: str


def comment_on(
    initial_fen: str,
    moves: Sequence[str],
    player_color: PlayerColor,
    detail_level: DetailLevel = DetailLevel.NORMAL,
    losses: Mapping[int, int] | None = None,
) -> Comment | None:
    """The remark the last move earned, or `None` for the usual silence.

    `losses` maps a player ply to the centipawns that move cost, as stored for a
    training game; a negative entry is a gain. An honest game passes nothing.
    """
    if not moves:
        return None
    emitted = _walk(initial_fen, moves, player_color, losses or {})
    if emitted is None or emitted[0] != len(moves) - 1:
        return None
    # Detail is a matter of taste and is applied to the answer only: the history
    # of what was said stays the same whenever the preference changed.
    if detail_level is DetailLevel.BRIEF:
        return None
    return emitted[1]


def _walk(
    initial_fen: str,
    moves: Sequence[str],
    player_color: PlayerColor,
    losses: Mapping[int, int],
) -> tuple[int, Comment] | None:
    """Replay the game and keep the last remark the cooldown actually allowed."""
    board = chess.Board(initial_fen)
    balances = [_balance(board)]
    stage = game_stage(board)
    opening = identify_opening(board)
    last: tuple[int, Comment] | None = None
    for ply, uci in enumerate(moves):
        move = chess.Move.from_uci(uci)
        board.push(move)
        balances.append(_balance(board))
        next_stage = game_stage(board)
        next_opening = identify_opening(board)
        candidate = _candidate(
            board,
            move,
            ply,
            player_color,
            balances,
            stage,
            next_stage,
            opening,
            next_opening,
            losses,
        )
        stage, opening = next_stage, next_opening
        if candidate is None:
            continue
        if last is not None and (ply - last[0] < COMMENT_COOLDOWN_PLIES or candidate.category is last[1].category):
            continue
        last = (ply, candidate)
    return last


def _candidate(
    board: chess.Board,
    move: chess.Move,
    ply: int,
    player_color: PlayerColor,
    balances: Sequence[int],
    stage: GameStage,
    next_stage: GameStage,
    opening: OpeningName | None,
    next_opening: OpeningName | None,
    losses: Mapping[int, int],
) -> Comment | None:
    """The one thing worth saying about this ply, by a fixed order of interest."""
    by_player = board.turn != player_color.to_chess()
    if move.promotion is not None:
        piece = PIECE_NAMES_ACCUSATIVE[move.promotion]
        side = "Ваша" if by_player else "Моя"
        return Comment(CommentCategory.PROMOTION, f"{side} пешка превратилась в {piece}.")
    swing = balances[ply + 1] - balances[max(0, ply - _SETTLING_PLIES + 1)]
    if abs(swing) >= MATERIAL_SWING:
        gained = (swing > 0) == (player_color is PlayerColor.WHITE)
        return Comment(
            CommentCategory.MATERIAL,
            "Вы выиграли материал." if gained else "Я выиграла материал.",
        )
    if board.is_check():
        return Comment(CommentCategory.CHECK, "Вам шах." if not by_player else "Вы объявили шах.")
    gain = -losses.get(ply, 0)
    if by_player and gain >= BLUNDER_CENTIPAWNS:
        return Comment(CommentCategory.EVALUATION, "Оценка позиции заметно изменилась в вашу пользу.")
    if next_stage is not stage:
        return Comment(CommentCategory.STAGE, _STAGE_ENTERED[next_stage])
    if next_opening is not None and next_opening != opening:
        return Comment(CommentCategory.OPENING, f"Это {next_opening.full_name}, код {next_opening.eco}.")
    return None


def _balance(board: chess.Board) -> int:
    """Material seen from White; the sign says which side is ahead."""
    return sum(
        value * (len(board.pieces(piece_type, chess.WHITE)) - len(board.pieces(piece_type, chess.BLACK)))
        for piece_type, value in _PIECE_VALUES.items()
    )
