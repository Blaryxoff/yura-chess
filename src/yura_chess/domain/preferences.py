"""Durable per-owner presentation preferences.

Preferences never change the chess meaning of a response: they only decide how
much is said, how it is punctuated, how a move is named and which side the board
is drawn from. Defaults here are the single source of truth for the domain; the
migration and the column defaults repeat the same values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from yura_chess.domain.game import PlayerColor


class DetailLevel(StrEnum):
    BRIEF = "brief"
    NORMAL = "normal"
    DETAILED = "detailed"


class PauseStyle(StrEnum):
    """How much punctuation the skill adds to its own speech.

    The physical speed of Alice's synthesis belongs to the platform: "говори
    медленнее" only adds pauses, and "говори быстрее" only removes the ones the
    skill added.
    """

    NORMAL = "normal"
    EXTENDED = "extended"


class NotationStyle(StrEnum):
    """FULL names both squares of a move, SHORT only the destination."""

    FULL = "full"
    SHORT = "short"


class BoardOrientation(StrEnum):
    """PLAYER follows the player's colour; WHITE and BLACK are pinned."""

    PLAYER = "player"
    WHITE = "white"
    BLACK = "black"


class GameMode(StrEnum):
    GAME = "game"
    TRAINING = "training"


DEFAULT_DETAIL_LEVEL = DetailLevel.NORMAL
DEFAULT_PAUSE_STYLE = PauseStyle.NORMAL
DEFAULT_NOTATION_STYLE = NotationStyle.FULL
DEFAULT_BOARD_ORIENTATION = BoardOrientation.PLAYER
DEFAULT_GAME_MODE = GameMode.GAME


@dataclass(frozen=True, slots=True)
class PlayerPreferences:
    owner_key: str
    detail_level: DetailLevel = DEFAULT_DETAIL_LEVEL
    pause_style: PauseStyle = DEFAULT_PAUSE_STYLE
    notation_style: NotationStyle = DEFAULT_NOTATION_STYLE
    board_orientation: BoardOrientation = DEFAULT_BOARD_ORIENTATION
    default_mode: GameMode = DEFAULT_GAME_MODE

    def orientation_for(self, player_color: PlayerColor | None) -> PlayerColor:
        """Which side the board is drawn from; white until a colour is chosen."""
        if self.board_orientation is BoardOrientation.WHITE:
            return PlayerColor.WHITE
        if self.board_orientation is BoardOrientation.BLACK:
            return PlayerColor.BLACK
        return player_color or PlayerColor.WHITE
