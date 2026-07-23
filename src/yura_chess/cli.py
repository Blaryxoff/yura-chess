"""Interactive and scripted voice-flow runner that does not require Alice."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import chess

from yura_chess.application.conversation import ConversationService, ConversationState
from yura_chess.application.game_service import RequestContext
from yura_chess.application.player_identity import owner_key
from yura_chess.domain.game import PlayerColor
from yura_chess.engine.stockfish import StockfishPool
from yura_chess.settings import Settings, get_settings
from yura_chess.storage.database import check_connection, check_schema, create_database_engine, create_session_factory

EXIT_COMMANDS = frozenset({"exit", "quit", "выход"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test «Шахматы с Юрой» without the Alice console")
    parser.add_argument("--command", action="append", default=[], help="Run one command; may be repeated")
    parser.add_argument("--script", type=Path, help="Read commands from a UTF-8 text file")
    parser.add_argument("--profile", default="default", help="Persistent shell player name")
    parser.add_argument("--show-fen", action="store_true", help="Print the resulting FEN after game responses")
    parser.add_argument("--show-board", action="store_true", help="Print the board after game responses")
    parser.add_argument(
        "--orientation",
        choices=("player", "white", "black"),
        default="player",
        help="Board orientation; player follows the selected game color",
    )
    return parser


async def run_shell(settings: Settings, args: argparse.Namespace) -> int:
    database = create_database_engine(settings)
    check_connection(database)
    check_schema(database)
    session_factory = create_session_factory(database)
    pool = StockfishPool(settings)
    await pool.start()
    try:
        conversation = ConversationService(session_factory, pool, settings)
        owner = owner_key(settings.identity_salt, f"shell:{args.profile}", None)
        session_id = f"shell-{uuid4()}"
        state = ConversationState()
        state = await _run_one(
            conversation,
            owner,
            session_id,
            0,
            "",
            state,
            args.show_fen,
            args.show_board,
            args.orientation,
            is_new_session=True,
        )
        commands = _commands(args)
        if commands is None:
            print("Шахматы с Юрой. Введите ход или команду; «выход» завершает сеанс.")
            message_id = 0
            while True:
                try:
                    utterance = input("Вы> ").strip()
                except EOFError:
                    break
                if utterance.lower() in EXIT_COMMANDS:
                    break
                message_id += 1
                state = await _run_one(
                    conversation,
                    owner,
                    session_id,
                    message_id,
                    utterance,
                    state,
                    args.show_fen,
                    args.show_board,
                    args.orientation,
                )
            return 0

        for message_id, utterance in enumerate(commands, start=1):
            print(f"Вы> {utterance}")
            if utterance.lower() in EXIT_COMMANDS:
                break
            state = await _run_one(
                conversation,
                owner,
                session_id,
                message_id,
                utterance,
                state,
                args.show_fen,
                args.show_board,
                args.orientation,
            )
        return 0
    finally:
        await pool.stop()
        database.dispose()


async def _run_one(
    conversation: ConversationService,
    owner: str,
    session_id: str,
    message_id: int,
    utterance: str,
    state: ConversationState,
    show_fen: bool,
    show_board: bool,
    orientation: str,
    is_new_session: bool = False,
) -> ConversationState:
    fingerprint = sha256(utterance.encode("utf-8")).hexdigest()
    request = RequestContext(
        "shell",
        session_id,
        str(message_id),
        fingerprint,
        is_new_session=is_new_session,
        traffic_source="test",
    )
    reply = await conversation.handle(owner, utterance, request, state)
    print(f"Юра> {reply.speech.text}")
    if reply.speech.tts is not None:
        print(f"TTS> {reply.speech.tts}")
    if show_fen and reply.turn is not None:
        print(f"FEN> {reply.turn.fen}")
    if show_board and reply.turn is not None:
        print("Доска>")
        print(format_board(reply.turn.fen, _board_orientation(orientation, reply.turn.player_color)))
    return reply.state


def format_board(fen: str, orientation: chess.Color = chess.WHITE) -> str:
    """Render a coordinate-labelled terminal board from the canonical FEN."""
    return chess.Board(fen).unicode(borders=True, empty_square="·", orientation=orientation)


def _board_orientation(value: str, player_color: PlayerColor) -> chess.Color:
    if value == "player":
        return player_color.to_chess()
    return chess.WHITE if value == "white" else chess.BLACK


def _commands(args: argparse.Namespace) -> list[str] | None:
    commands = list(args.command)
    if args.script is not None:
        commands.extend(_script_lines(args.script))
    return commands or None


def _script_lines(path: Path) -> Iterable[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        command = line.strip()
        if command and not command.startswith("#"):
            yield command


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run_shell(get_settings(), args)))


if __name__ == "__main__":  # pragma: no cover
    main()
