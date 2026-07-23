"""Turn a `TurnResult` into the two strings a voice reply consists of.

Every answer is complete as speech alone: nothing here depends on a screen, a
card or an image, so a voice-only device gets the full state of the game. The
`tts` string is produced only when the pronunciation differs from the display
text — otherwise Alice speaks the text itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import chess

from yura_chess.domain.game import PlayerColor
from yura_chess.domain.preferences import NotationStyle
from yura_chess.domain.results import GameEnd, GameOutcome, TurnResult, TurnStatus
from yura_chess.presentation import help_speech
from yura_chess.presentation.board_image import position_hash, render_png
from yura_chess.presentation.move_speech import Speech, describe_move, describe_played_move

# How much a single Alice `ItemsList` card can carry.
CARD_DESCRIPTION_LIMIT = 256
CARD_ITEMS_LIMIT = 5

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


def compose_turn(
    result: TurnResult,
    board_before: chess.Board | None = None,
    notation: NotationStyle = NotationStyle.FULL,
    commentary: str | None = None,
) -> Speech:
    """Say what the turn did; `board_before` is the position the engine moved in.

    `commentary` is the optional remark about the move and always comes last: it
    is an aside, never part of what happened.
    """
    move_text = _move_text(result, board_before, notation)
    outcome_text = _outcome_text(result)
    if result.outcome is not None and result.outcome.end is GameEnd.CHECKMATE:
        move_text = move_text.removesuffix(" Мат.")
    if commentary is not None and "Шах." in move_text and "шах" in commentary.lower():
        commentary = None
    parts = [text for text in (move_text, outcome_text, commentary) if text]
    if not parts:
        return Speech.of(_STATUS_TEXTS.get(result.status, "Ваш ход."))
    return Speech.of(" ".join(parts))


@dataclass(frozen=True, slots=True)
class BoardCard:
    """A card the adapter may attach — never a card it has to attach."""

    position_hash: str
    render: Callable[[], bytes]
    title: str


def compose_board_card(
    result: TurnResult,
    has_screen: bool,
    orientation: PlayerColor | None = None,
) -> BoardCard | None:
    """Describe the picture of the position, or nothing if no screen will show it.

    `orientation` is the side the board is drawn from; without a stored
    preference it is the player's own colour.
    """
    if not has_screen:
        return None
    board = chess.Board(result.fen)
    last_move = result.engine_move or result.player_move
    drawn_from = orientation or result.player_color
    return BoardCard(
        position_hash=position_hash(board, drawn_from, last_move),
        render=partial(render_png, board, drawn_from, last_move),
        title="Ваш ход" if board.turn == _chess_color(result.player_color) else "Мой ход",
    )


def compose_position_card(
    board: chess.Board,
    orientation: PlayerColor,
    last_move_uci: str | None,
    title: str,
) -> BoardCard:
    """The same picture for a position that belongs to no game, such as a puzzle."""
    return BoardCard(
        position_hash=position_hash(board, orientation, last_move_uci),
        render=partial(render_png, board, orientation, last_move_uci),
        title=title,
    )


@dataclass(frozen=True, slots=True)
class TextCard:
    """A screen-only repetition of what was already said; never new information."""

    header: str
    items: tuple[str, ...]


def compose_help_card() -> TextCard:
    """The topics help can read, listed for a screen that is already showing them."""
    titles = tuple(f"«{section.title}»" for section in help_speech.SECTIONS)
    return TextCard("Справка", _packed(titles, ", "))


def compose_pgn_card(export: str) -> TextCard:
    """The PGN text or its explicitly labelled preview, repeated on screen.

    Text is split on token boundaries so the card never cuts a token in half.
    The review service labels long exports as previews before they reach here.
    """
    return TextCard("PGN", _packed(tuple(export.split()), " "))


def _packed(parts: tuple[str, ...], separator: str) -> tuple[str, ...]:
    """Greedily fill the card items so no part is ever cut in the middle.

    A card holds a fixed number of items of a fixed width; parts that no longer
    fit are dropped rather than truncated, because half a token is worse than a
    missing one on a screen that only repeats what was already said.
    """
    items: list[str] = []
    for part in parts:
        if items and len(items[-1]) + len(separator) + len(part) <= CARD_DESCRIPTION_LIMIT:
            items[-1] = f"{items[-1]}{separator}{part}"
            continue
        if len(items) == CARD_ITEMS_LIMIT:
            break
        items.append(part[:CARD_DESCRIPTION_LIMIT])
    return tuple(items)


def _chess_color(color: PlayerColor) -> chess.Color:
    return chess.WHITE if color is PlayerColor.WHITE else chess.BLACK


def _move_text(result: TurnResult, board_before: chess.Board | None, notation: NotationStyle) -> str:
    if result.status is TurnStatus.ENGINE_UNAVAILABLE:
        return _STATUS_TEXTS[TurnStatus.ENGINE_UNAVAILABLE]
    if result.engine_move is None:
        return ""
    move = chess.Move.from_uci(result.engine_move)
    if board_before is not None:
        return "Мой ход. " + describe_move(board_before, move, notation).text
    # Without the previous position the moving piece is still readable off the
    # destination square; only what it captured is lost.
    return "Мой ход. " + describe_played_move(chess.Board(result.fen), move, notation).text


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
