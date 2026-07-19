"""The puzzle importer is offline and reproducible: same source, same bytes.

Every test reads the committed sample instead of the 300 MB Lichess dump; the
dump is only ever used to regenerate the shipped catalogue by hand.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import chess
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "import_lichess_puzzles.py"
SAMPLE = ROOT / "tests" / "fixtures" / "lichess_puzzles_sample.csv"
VERSION = "2026-07"


def load_importer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_lichess_puzzles", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `scripts/` is not a package, and `@dataclass` needs the module in `sys.modules` to resolve annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


importer = load_importer()


def run(output_dir: Path, source: Path = SAMPLE, per_theme_and_bucket: int = 2) -> list[dict[str, object]]:
    importer.import_puzzles(source, VERSION, output_dir, per_theme_and_bucket)
    lines = (output_dir / "puzzles.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def rewrite_sample(directory: Path, puzzle_id: str, **overrides: str) -> Path:
    """Copy the sample with one row edited, so a single defect can be isolated."""
    path = directory / "source.csv"
    with SAMPLE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["PuzzleId"] == puzzle_id:
            row.update(overrides)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=importer.HEADER)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_a_valid_row_keeps_the_whole_line_including_the_forced_reply(tmp_path: Path) -> None:
    puzzles = {p["id"]: p for p in run(tmp_path / "out")}

    assert puzzles["BBbb02"]["moves"] == ["g8f8", "c4f4", "f8g8", "e1e8"]
    assert puzzles["BBbb02"]["rating"] == 1600
    assert puzzles["BBbb02"]["themes"] == ["mateIn2", "short"]


def test_each_puzzle_lands_in_its_rating_bucket(tmp_path: Path) -> None:
    puzzles = {p["id"]: p for p in run(tmp_path / "out")}

    assert puzzles["AAaa01"]["bucket"] == "low"
    assert puzzles["EEee05"]["bucket"] == "medium"
    assert puzzles["DDdd04"]["bucket"] == "high"


def test_the_catalogue_is_ordered_by_puzzle_id(tmp_path: Path) -> None:
    ids = [p["id"] for p in run(tmp_path / "out")]

    assert ids == sorted(ids)


@pytest.mark.parametrize(
    ("puzzle_id", "why"),
    [
        ("FFff06", "no allowlisted theme"),
        ("GGgg07", "rating outside every bucket"),
        ("HHhh08", "illegal move in the line"),
        ("IIii09", "unparsable FEN"),
        ("JJjj10", "mateIn1 that does not mate"),
        ("KKkk11", "line ending on an opponent reply"),
    ],
)
def test_an_untrustworthy_or_unwanted_row_is_left_out(tmp_path: Path, puzzle_id: str, why: str) -> None:
    ids = {p["id"] for p in run(tmp_path / "out")}

    assert puzzle_id not in ids, why
    assert ids, "the rest of the sample must still import"


def test_a_solution_longer_than_the_voice_limit_is_left_out(tmp_path: Path) -> None:
    long_line = " ".join(["e8d8", "g1f3", "d8c8", "f3g1", "c8b8", "g1f3", "b8a8", "f3g1", "a8b8", "g1f3"])
    source = rewrite_sample(tmp_path, "CCcc03", Moves=long_line)

    ids = {p["id"] for p in run(tmp_path / "out", source)}

    assert "CCcc03" not in ids


def test_selection_is_balanced_across_themes_and_buckets(tmp_path: Path) -> None:
    puzzles = run(tmp_path / "out", per_theme_and_bucket=1)

    per_group: dict[tuple[str, str], int] = {}
    for puzzle in puzzles:
        for theme in puzzle["themes"]:
            if theme in importer.THEME_ALLOWLIST:
                key = (theme, str(puzzle["bucket"]))
                per_group[key] = per_group.get(key, 0) + 1
    assert all(count <= 1 for count in per_group.values())


def test_a_second_import_of_the_same_source_is_byte_identical(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"

    importer.import_puzzles(SAMPLE, VERSION, first, 2)
    importer.import_puzzles(SAMPLE, VERSION, second, 2)

    for name in ("puzzles.jsonl", "puzzles.meta.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_the_metadata_records_provenance_and_the_output_hash(tmp_path: Path) -> None:
    out = tmp_path / "out"
    puzzles = run(out)

    meta = json.loads((out / "puzzles.meta.json").read_text(encoding="utf-8"))

    assert meta["source_url"] == "https://database.lichess.org/lichess_db_puzzle.csv.zst"
    assert meta["source_version"] == VERSION
    assert meta["source_sha256"] == "sha256:" + hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    assert meta["license"] == "CC0-1.0"
    assert meta["license_url"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert meta["theme_allowlist"] == list(importer.THEME_ALLOWLIST)
    assert meta["rating_buckets"] == {"low": [600, 1400], "medium": [1401, 1800], "high": [1801, 2400]}
    assert meta["puzzle_count"] == len(puzzles)
    assert meta["output_hash"] == "sha256:" + hashlib.sha256((out / "puzzles.jsonl").read_bytes()).hexdigest()


def test_a_source_version_that_is_not_a_snapshot_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(importer.PuzzleImportError, match="YYYY-MM snapshot"):
        importer.import_puzzles(SAMPLE, "july", tmp_path / "out")


def test_a_missing_source_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(importer.PuzzleImportError, match="missing source file"):
        importer.import_puzzles(tmp_path / "absent.csv", VERSION, tmp_path / "out")


def test_a_source_with_an_unexpected_header_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("PuzzleId,FEN,Moves\n", encoding="utf-8", newline="\n")

    with pytest.raises(importer.PuzzleImportError, match="unexpected header"):
        importer.import_puzzles(source, VERSION, tmp_path / "out")


def test_a_source_without_a_single_usable_row_is_rejected(tmp_path: Path) -> None:
    source = rewrite_sample(tmp_path, "", Themes="endgame")
    rows = source.read_text(encoding="utf-8").splitlines()
    source.write_text("\n".join([rows[0]] + [row for row in rows[1:] if row.startswith("FFff06")]) + "\n")

    with pytest.raises(importer.PuzzleImportError, match="no usable puzzles"):
        importer.import_puzzles(source, VERSION, tmp_path / "out")


def test_the_shipped_catalogue_matches_the_recorded_hash_and_metadata() -> None:
    data = Path(importer.DEFAULT_OUTPUT_DIR)
    meta = json.loads((data / "puzzles.meta.json").read_text(encoding="utf-8"))
    jsonl = (data / "puzzles.jsonl").read_bytes()

    assert meta["output_hash"] == "sha256:" + hashlib.sha256(jsonl).hexdigest()
    assert meta["puzzle_count"] == len(jsonl.decode("utf-8").splitlines())


def test_every_shipped_puzzle_replays_legally_and_is_within_the_allowlist() -> None:
    data = Path(importer.DEFAULT_OUTPUT_DIR)
    meta = json.loads((data / "puzzles.meta.json").read_text(encoding="utf-8"))
    puzzles = [json.loads(line) for line in (data / "puzzles.jsonl").read_text(encoding="utf-8").splitlines()]

    assert puzzles, "the shipped catalogue must not be empty"
    for puzzle in puzzles:
        board = chess.Board(puzzle["fen"])
        for uci in puzzle["moves"]:
            board.push_uci(uci)
        assert len(puzzle["moves"]) % 2 == 0
        assert any(theme in importer.THEME_ALLOWLIST for theme in puzzle["themes"])
        low, high = meta["rating_buckets"][puzzle["bucket"]]
        assert low <= puzzle["rating"] <= high
        if "mateIn1" in puzzle["themes"] or "mateIn2" in puzzle["themes"]:
            assert board.is_checkmate()
