"""The ECO importer is offline and reproducible: same source, same bytes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "import_lichess_openings.py"
REVISION = "292fd0468068f58bb244f7fe1c3e573e493c3c53"
SAMPLE = {
    "a.tsv": [("A00", "Amar Opening", "1. Nh3"), ("A00", "Amar Opening: Paris Gambit", "1. Nh3 d5 2. g3 e5 3. f4")],
    "b.tsv": [("B00", "King's Pawn Game", "1. e4")],
    "c.tsv": [("C50", "Italian Game", "1. e4 e5 2. Nf3 Nc6 3. Bc4")],
    "d.tsv": [("D00", "Queen's Pawn Game", "1. d4 d5")],
    "e.tsv": [("E00", "Indian Defense", "1. d4 Nf6 2. c4 e6")],
}


def load_importer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_lichess_openings", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `scripts/` is not a package, and `@dataclass` needs the module in `sys.modules` to resolve annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


importer = load_importer()


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "source"
    directory.mkdir()
    write_source(directory, SAMPLE)
    return directory


def write_source(directory: Path, tables: dict[str, list[tuple[str, str, str]]]) -> None:
    for name, rows in tables.items():
        lines = ["eco\tname\tpgn"] + ["\t".join(row) for row in rows]
        (directory / name).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def test_the_importer_converts_san_lines_into_uci_prefixes(source_dir: Path, tmp_path: Path) -> None:
    importer.import_openings(source_dir, REVISION, tmp_path / "out")

    rows = (tmp_path / "out" / "openings.tsv").read_text(encoding="utf-8").splitlines()

    assert rows[0] == "eco\topening\tvariation\tuci"
    assert rows[1] == "A00\tAmar Opening\t\tg1h3"
    assert rows[2] == "A00\tAmar Opening\tParis Gambit\tg1h3 d7d5 g2g3 e7e5 f2f4"
    assert rows[4] == "C50\tItalian Game\t\te2e4 e7e5 g1f3 b8c6 f1c4"


def test_every_source_table_is_read_and_sorted_into_one_output(source_dir: Path, tmp_path: Path) -> None:
    importer.import_openings(source_dir, REVISION, tmp_path / "out")

    rows = (tmp_path / "out" / "openings.tsv").read_text(encoding="utf-8").splitlines()[1:]

    assert [row.split("\t")[0] for row in rows] == ["A00", "A00", "B00", "C50", "D00", "E00"]


def test_a_second_import_of_the_same_source_is_byte_identical(source_dir: Path, tmp_path: Path) -> None:
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()

    importer.import_openings(source_dir, REVISION, first)
    importer.import_openings(source_dir, REVISION, second)

    for name in ("openings.tsv", "openings.meta.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_the_metadata_records_provenance_and_the_output_hash(source_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    importer.import_openings(source_dir, REVISION, out)

    meta = json.loads((out / "openings.meta.json").read_text(encoding="utf-8"))

    assert meta["source_revision"] == REVISION
    assert meta["source_repository"] == "https://github.com/lichess-org/chess-openings"
    assert meta["license"] == "CC0-1.0"
    assert meta["license_url"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert meta["opening_count"] == 6
    assert meta["output_hash"] == "sha256:" + hashlib.sha256((out / "openings.tsv").read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([("F00", "Nonexistent Volume", "1. e4")], "bad ECO code"),
        ([("A00", "", "1. e4")], "empty opening name"),
        ([("A00", "Impossible Opening", "1. e5")], "cannot play"),
        ([("A00", "Garbled Opening", "1. Qh9")], "cannot play"),
        ([("A00", "Empty Opening", "")], "empty move list"),
    ],
)
def test_an_untrustworthy_row_is_rejected_before_anything_is_written(
    tmp_path: Path, rows: list[tuple[str, str, str]], message: str
) -> None:
    directory = tmp_path / "source"
    directory.mkdir()
    write_source(directory, dict(SAMPLE) | {"a.tsv": rows})
    out = tmp_path / "out"

    with pytest.raises(importer.OpeningImportError, match=message):
        importer.import_openings(directory, REVISION, out)

    assert not out.exists()


def test_a_source_file_with_an_unexpected_header_is_rejected(source_dir: Path, tmp_path: Path) -> None:
    (source_dir / "a.tsv").write_text("eco\tname\n", encoding="utf-8", newline="\n")

    with pytest.raises(importer.OpeningImportError, match="unexpected header"):
        importer.import_openings(source_dir, REVISION, tmp_path / "out")


def test_a_missing_source_file_is_rejected(source_dir: Path, tmp_path: Path) -> None:
    (source_dir / "e.tsv").unlink()

    with pytest.raises(importer.OpeningImportError, match="missing source file"):
        importer.import_openings(source_dir, REVISION, tmp_path / "out")


def test_a_short_source_revision_is_rejected(source_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(importer.OpeningImportError, match="full git sha"):
        importer.import_openings(source_dir, "292fd04", tmp_path / "out")


def test_the_shipped_set_matches_the_recorded_hash_and_pinned_revision() -> None:
    data = Path(importer.DEFAULT_OUTPUT_DIR)
    meta = json.loads((data / "openings.meta.json").read_text(encoding="utf-8"))
    tsv = (data / "openings.tsv").read_bytes()

    assert meta["source_revision"] == REVISION
    assert meta["output_hash"] == "sha256:" + hashlib.sha256(tsv).hexdigest()
    assert meta["opening_count"] == len(tsv.decode("utf-8").splitlines()) - 1
