from __future__ import annotations

import asyncio
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import chess
import pytest
from settings_fixtures import TEST_IDENTITY_SALT, UNREACHABLE_DATABASE_URL
from sqlalchemy.orm import Session, sessionmaker

from yura_chess import cli
from yura_chess.application.conversation import ConversationService, ConversationState
from yura_chess.application.player_identity import owner_key
from yura_chess.presentation.help_speech import HelpState, HelpTopic
from yura_chess.settings import Settings


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


def test_shell_keeps_the_open_help_between_scripted_commands(
    session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        environment="test",
        database_url=UNREACHABLE_DATABASE_URL,
        identity_salt=TEST_IDENTITY_SALT,
    )
    conversation = ConversationService(session_factory, _NoEngine(), settings)
    owner = owner_key(settings.identity_salt, "shell:help-topics", None)

    async def run() -> list[ConversationState]:
        state = ConversationState()
        states: list[ConversationState] = []
        for message_id, utterance in enumerate(["", "справка", "партия", "дальше", "какая позиция"]):
            state = await cli._run_one(
                conversation,
                owner,
                "shell-help-topics",
                message_id,
                utterance,
                state,
                False,
                False,
                "player",
                is_new_session=message_id == 0,
            )
            states.append(state)
        return states

    _, menu, topic, paged, position = asyncio.run(run())

    assert menu.help == HelpState(topic=None, page=0)
    assert topic.help == HelpState(topic=HelpTopic.GAME, page=0)
    assert paged.help == HelpState(topic=HelpTopic.GAME, page=1)
    # A board question closes the help, so «дальше» goes back to reading the board.
    assert position.help is None
    printed = capsys.readouterr().out
    assert "Разделы справки" in printed
    assert "Раздел «партия»" in printed


class _NoEngine:
    """The scripted help flow must never need a move search."""

    async def best_move(
        self,
        board: chess.Board,
        search_time: float | None = None,
        skill_level: int | None = None,
    ) -> str:
        raise AssertionError("help must not start an engine search")


def test_help_does_not_require_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["yura-chess-shell", "--help"])
    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("settings must not load for --help"))

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 0
