from argparse import Namespace
from pathlib import Path

from yura_chess.cli import _commands


def test_scripted_commands_preserve_order_and_ignore_comments(tmp_path: Path) -> None:
    script = tmp_path / "game.txt"
    script.write_text("# opening\nпешка е два е четыре\n\nкакая позиция\n", encoding="utf-8")
    args = Namespace(command=["начать игру"], script=script)

    assert _commands(args) == ["начать игру", "пешка е два е четыре", "какая позиция"]


def test_no_script_or_commands_selects_interactive_mode() -> None:
    assert _commands(Namespace(command=[], script=None)) is None
