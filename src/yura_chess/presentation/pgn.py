"""Export a finished game as PGN and read the same moves aloud.

The canonical game is still the starting FEN plus the UCI history: the PGN is
derived from it and never stored, so exporting one cannot change a game. The
spoken pages carry exactly the same moves as the export, because both walk the
same history — a screen only ever repeats what the voice already said.
"""

from __future__ import annotations

import chess
import chess.pgn

from yura_chess.domain.game import START_FEN, GameState, PlayerColor
from yura_chess.domain.results import GameOutcome
from yura_chess.presentation.move_speech import describe_move

# Three full moves is about as much as stays in the ear from one reply.
MOVES_PER_PAGE = 3

_PLAYER_NAME = "Player"
_ENGINE_NAME = "Yura"


def result_token(outcome: GameOutcome | None) -> str:
    """The PGN result tag; an unfinished or unknown ending stays `*`."""
    if outcome is None:
        return "*"
    if outcome.winner is None:
        return "1/2-1/2"
    return "1-0" if outcome.winner is PlayerColor.WHITE else "0-1"


def export(game: GameState, outcome: GameOutcome | None) -> str:
    """Render the whole game as standards-compliant PGN.

    A game that did not start from the initial array carries the `SetUp` and
    `FEN` tags, so re-reading the export replays into the same final position.
    """
    exported = chess.pgn.Game()
    white, black = (
        (_PLAYER_NAME, _ENGINE_NAME) if game.player_color is PlayerColor.WHITE else (_ENGINE_NAME, _PLAYER_NAME)
    )
    exported.headers["Event"] = "Yura Chess"
    exported.headers["Site"] = "yura-chess"
    exported.headers["Date"] = game.created_at.strftime("%Y.%m.%d")
    exported.headers["Round"] = "-"
    exported.headers["White"] = white
    exported.headers["Black"] = black
    exported.headers["Result"] = result_token(outcome)
    if game.initial_fen != START_FEN:
        exported.headers["SetUp"] = "1"
        exported.headers["FEN"] = game.initial_fen
        exported.setup(chess.Board(game.initial_fen))
    node: chess.pgn.GameNode = exported
    for uci in game.moves:
        node = node.add_main_variation(chess.Move.from_uci(uci))
    return str(exported)


def move_lines(game: GameState) -> tuple[str, ...]:
    """One spoken line per full move, White's move and Black's answer together."""
    board = chess.Board(game.initial_fen)
    lines: list[str] = []
    pending_number: int | None = None
    pending_white: str | None = None
    for uci in game.moves:
        move = chess.Move.from_uci(uci)
        number = board.fullmove_number
        white_to_move = board.turn == chess.WHITE
        described = describe_move(board, move).text.rstrip(".")
        board.push(move)
        if white_to_move:
            pending_number, pending_white = number, described
            continue
        if pending_white is None:
            # The history starts on Black's move, so this line has no White half.
            lines.append(f"Ход {number}, черные: {described}.")
        else:
            lines.append(f"Ход {pending_number}: {pending_white}, в ответ {described}.")
        pending_white = None
    if pending_white is not None:
        lines.append(f"Ход {pending_number}: {pending_white}.")
    return tuple(lines)


def move_pages(game: GameState) -> tuple[tuple[str, ...], ...]:
    """The spoken lines split into pages; an empty game still has one page."""
    lines = move_lines(game)
    if not lines:
        return ((),)
    return tuple(lines[start : start + MOVES_PER_PAGE] for start in range(0, len(lines), MOVES_PER_PAGE))
