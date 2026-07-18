"""Render the current position as a PNG, in memory only.

Nothing here touches the filesystem: the card is a transient artefact of one
request, and Firebat must never accumulate board images. The position hash
covers every input that changes a pixel — placement, orientation and the
highlighted last move — because two of those three are absent from the FEN.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO

import chess
from PIL import Image, ImageDraw, ImageFont

from yura_chess.domain.game import PlayerColor

POSITION_HASH_LENGTH = 64
SQUARE_PIXELS = 64
BORDER_PIXELS = 20
BOARD_PIXELS = SQUARE_PIXELS * 8 + BORDER_PIXELS * 2

_LIGHT = (238, 219, 181)
_DARK = (181, 136, 99)
_HIGHLIGHT = (246, 246, 105)
_BORDER = (48, 44, 40)
_WHITE_PIECE = (255, 255, 255)
_BLACK_PIECE = (26, 26, 26)
_OUTLINE = (90, 90, 90)

# `load_default` returns either variant depending on how Pillow was built.
_Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

_LETTERS = {
    chess.PAWN: "P",
    chess.KNIGHT: "N",
    chess.BISHOP: "B",
    chess.ROOK: "R",
    chess.QUEEN: "Q",
    chess.KING: "K",
}


def position_hash(board: chess.Board, orientation: PlayerColor, last_move_uci: str | None) -> str:
    """Stable identity of the rendered image, not of the game."""
    source = f"{board.board_fen()}|{orientation.value}|{last_move_uci or ''}"
    return sha256(source.encode("utf-8")).hexdigest()[:POSITION_HASH_LENGTH]


def render_png(board: chess.Board, orientation: PlayerColor, last_move_uci: str | None = None) -> bytes:
    """Draw the board from the player's side of the table into a byte string."""
    highlighted = _highlighted_squares(last_move_uci)
    image = Image.new("RGB", (BOARD_PIXELS, BOARD_PIXELS), _BORDER)
    canvas = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=SQUARE_PIXELS // 2)

    for square in chess.SQUARES:
        left, top = _square_origin(square, orientation)
        box = (left, top, left + SQUARE_PIXELS - 1, top + SQUARE_PIXELS - 1)
        canvas.rectangle(box, fill=_square_color(square, square in highlighted))
        piece = board.piece_at(square)
        if piece is not None:
            _draw_piece(canvas, piece, left, top, font)

    _draw_coordinates(canvas, orientation)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _highlighted_squares(last_move_uci: str | None) -> set[int]:
    if not last_move_uci:
        return set()
    try:
        move = chess.Move.from_uci(last_move_uci)
    except ValueError:
        # A malformed move only costs the highlight; the board still renders.
        return set()
    return {move.from_square, move.to_square}


def _square_origin(square: int, orientation: PlayerColor) -> tuple[int, int]:
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)
    if orientation is PlayerColor.WHITE:
        column, row = file_index, 7 - rank_index
    else:
        column, row = 7 - file_index, rank_index
    return BORDER_PIXELS + column * SQUARE_PIXELS, BORDER_PIXELS + row * SQUARE_PIXELS


def _square_color(square: int, highlighted: bool) -> tuple[int, int, int]:
    if highlighted:
        return _HIGHLIGHT
    return _LIGHT if (chess.square_file(square) + chess.square_rank(square)) % 2 else _DARK


def _draw_piece(canvas: ImageDraw.ImageDraw, piece: chess.Piece, left: int, top: int, font: _Font) -> None:
    center = (left + SQUARE_PIXELS // 2, top + SQUARE_PIXELS // 2)
    fill = _WHITE_PIECE if piece.color is chess.WHITE else _BLACK_PIECE
    canvas.text(
        center,
        _LETTERS[piece.piece_type],
        fill=fill,
        font=font,
        anchor="mm",
        stroke_width=1,
        stroke_fill=_OUTLINE,
    )


def _draw_coordinates(canvas: ImageDraw.ImageDraw, orientation: PlayerColor) -> None:
    font = ImageFont.load_default(size=BORDER_PIXELS - 6)
    files = chess.FILE_NAMES if orientation is PlayerColor.WHITE else tuple(reversed(chess.FILE_NAMES))
    ranks = tuple(reversed(chess.RANK_NAMES)) if orientation is PlayerColor.WHITE else chess.RANK_NAMES
    for index in range(8):
        offset = BORDER_PIXELS + index * SQUARE_PIXELS + SQUARE_PIXELS // 2
        canvas.text((offset, BOARD_PIXELS - BORDER_PIXELS // 2), files[index], fill=_LIGHT, font=font, anchor="mm")
        canvas.text((BORDER_PIXELS // 2, offset), ranks[index], fill=_LIGHT, font=font, anchor="mm")
