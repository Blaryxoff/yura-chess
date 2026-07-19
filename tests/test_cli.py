from __future__ import annotations

import asyncio
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import chess
import pytest

from yura_chess import cli
from yura_chess.application.conversation import ConversationState


def test_scripted_commands_preserve_order_and_ignore_comments(tmp_path: Path) -> None:
    script = tmp_path / "game.txt"
    script.write_text("# opening\nпешка е два е четыре\n\nкакая позиция\n", encoding="utf-8")
    args = Namespace(command=["начать игру"], script=script)

    assert cli._commands(args) == ["начать игру", "пешка е два е четыре", "какая позиция"]


def test_no_script_or_commands_selects_interactive_mode() -> None:
    assert cli._commands(Namespace(command=[], script=None)) is None


def test_terminal_board_has_coordinates_and_pieces() -> None:
    rendered = cli.format_board(chess.STARTING_FEN)

    assert "8 |♜|♞|♝|♛|♚|♝|♞|♜|" in rendered
    assert "1 |♖|♘|♗|♕|♔|♗|♘|♖|" in rendered
    assert "a b c d e f g h" in rendered


def test_terminal_board_can_be_oriented_for_black() -> None:
    rendered = cli.format_board(chess.STARTING_FEN, chess.BLACK)

    assert "1 |♖|♘|♗|♔|♕|♗|♘|♖|" in rendered
    assert "8 |♜|♞|♝|♚|♛|♝|♞|♜|" in rendered
    assert "h g f e d c b a" in rendered


def test_shell_bootstraps_an_alice_new_session_before_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str, bool]] = []

    class FakeDatabase:
        def dispose(self) -> None:
            pass

    class FakePool:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    async def fake_run_one(
        conversation: object,
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
        calls.append((message_id, utterance, is_new_session))
        return state

    monkeypatch.setattr(cli, "create_database_engine", lambda settings: FakeDatabase())
    monkeypatch.setattr(cli, "check_connection", lambda database: None)
    monkeypatch.setattr(cli, "check_schema", lambda database: None)
    monkeypatch.setattr(cli, "create_session_factory", lambda database: object())
    monkeypatch.setattr(cli, "StockfishPool", lambda settings: FakePool())
    monkeypatch.setattr(cli, "ConversationService", lambda session_factory, pool, settings: object())
    monkeypatch.setattr(cli, "owner_key", lambda salt, user_id, application_id: "owner")
    monkeypatch.setattr(cli, "_run_one", fake_run_one)
    args = Namespace(
        command=["где белые слоны"],
        script=None,
        profile="resume-test",
        show_fen=False,
        show_board=False,
        orientation="player",
    )

    result = asyncio.run(cli.run_shell(SimpleNamespace(identity_salt="salt"), args))  # type: ignore[arg-type]

    assert result == 0
    assert calls == [(0, "", True), (1, "где белые слоны", False)]


def test_help_does_not_require_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["yura-chess-shell", "--help"])
    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("settings must not load for --help"))

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 0
