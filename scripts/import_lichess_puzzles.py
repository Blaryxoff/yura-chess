"""Convert the Lichess CC0 puzzle database into the compact catalogue the skill ships.

The importer is offline on purpose: it reads a local, already decompressed copy
of `lichess_db_puzzle.csv` pinned to one dated snapshot, and runtime never
touches the network. Running it twice on the same source must produce
byte-identical output, so the selection is a pure function of the source rows:
survivors are ordered by `PuzzleId`, never sampled, and never cut off by the
order the stream happens to arrive in.

Nothing here consults an engine. A puzzle is trusted because its whole line is
legal and, for a `mateInN` theme, ends in mate — not because a local Stockfish
happened to agree with it.

Usage:

    uv run python scripts/import_lichess_puzzles.py \\
        --source-csv /path/to/lichess_db_puzzle.csv \\
        --source-version 2026-07
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import chess

SOURCE_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
LICENSE = "CC0-1.0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
HEADER = [
    "PuzzleId",
    "FEN",
    "Moves",
    "Rating",
    "RatingDeviation",
    "Popularity",
    "NbPlays",
    "Themes",
    "GameUrl",
    "OpeningTags",
]
PUZZLE_ID_PATTERN = re.compile(r"^[0-9A-Za-z]{4,8}$")
SOURCE_VERSION_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}$")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "yura_chess" / "data"

# Themes a voice player can recognise by ear. Order is fixed: it drives selection.
THEME_ALLOWLIST = (
    "mateIn1",
    "mateIn2",
    "fork",
    "pin",
    "skewer",
    "hangingPiece",
    "discoveredAttack",
    "backRankMate",
)
# Task 14 buckets, as inclusive rating ranges.
BUCKETS = (("low", 600, 1400), ("medium", 1401, 1800), ("high", 1801, 2400))
PER_THEME_AND_BUCKET = 12
# A voice solver holds a short line in their head; anything longer is a different exercise.
MAX_SOLVER_MOVES = 4
COLUMNS = ("id", "fen", "moves", "rating", "themes", "bucket")


class PuzzleImportError(ValueError):
    """A structural failure in the source; the importer writes nothing."""


@dataclass(frozen=True, slots=True)
class Puzzle:
    id: str
    fen: str
    moves: tuple[str, ...]
    rating: int
    themes: tuple[str, ...]
    bucket: str

    def as_row(self) -> dict[str, object]:
        return {
            "id": self.id,
            "fen": self.fen,
            # The full line, opponent replies included: solving needs the forced answer.
            "moves": list(self.moves),
            "rating": self.rating,
            "themes": list(self.themes),
            "bucket": self.bucket,
        }


def bucket_for(rating: int) -> str | None:
    for name, low, high in BUCKETS:
        if low <= rating <= high:
            return name
    return None


def parse_row(row: dict[str, str]) -> Puzzle | None:
    """Return the puzzle, or `None` for any row this catalogue does not want.

    A five-million-row dump legitimately contains rows we skip; only a broken
    file shape is an error.
    """
    puzzle_id = row["PuzzleId"].strip()
    if not PUZZLE_ID_PATTERN.match(puzzle_id):
        return None
    themes = tuple(sorted(theme for theme in row["Themes"].split() if theme))
    if not any(theme in THEME_ALLOWLIST for theme in themes):
        return None
    try:
        rating = int(row["Rating"])
    except ValueError:
        return None
    bucket = bucket_for(rating)
    if bucket is None:
        return None
    moves = validate_line(row["FEN"].strip(), row["Moves"].split(), themes)
    if moves is None:
        return None
    return Puzzle(id=puzzle_id, fen=row["FEN"].strip(), moves=moves, rating=rating, themes=themes, bucket=bucket)


def validate_line(fen: str, moves: list[str], themes: tuple[str, ...]) -> tuple[str, ...] | None:
    """Replay the whole line so an unplayable puzzle never reaches the catalogue.

    The first move is the opponent's setup move played from `fen`; the solver
    answers it, so solver moves sit at the odd indices.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        return None
    if not board.is_valid():
        return None
    if len(moves) % 2 == 1:
        # The setup move plus an odd number of plies would end on an opponent
        # reply, leaving the solver without the last word.
        return None
    reply_plies = len(moves) - 1
    if reply_plies < 1 or reply_plies > MAX_SOLVER_MOVES * 2 - 1:
        return None
    for uci in moves:
        try:
            board.push_uci(uci)
        except (ValueError, AssertionError):
            return None
    for theme, expected in (("mateIn1", 1), ("mateIn2", 2)):
        if theme in themes and (reply_plies != expected * 2 - 1 or not board.is_checkmate()):
            return None
    return tuple(moves)


def read_source(source_csv: Path) -> list[Puzzle]:
    if not source_csv.is_file():
        raise PuzzleImportError(f"missing source file {source_csv}")
    puzzles: list[Puzzle] = []
    with source_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != HEADER:
            raise PuzzleImportError(f"unexpected header {header!r}")
        for row in reader:
            if not row:
                continue
            if len(row) != len(HEADER):
                continue
            puzzle = parse_row(dict(zip(HEADER, row, strict=True)))
            if puzzle is not None:
                puzzles.append(puzzle)
    if not puzzles:
        raise PuzzleImportError(f"no usable puzzles found in {source_csv}")
    return puzzles


def select(puzzles: list[Puzzle], per_theme_and_bucket: int) -> list[Puzzle]:
    """Take a fixed, balanced slice; `PuzzleId` order makes the choice reproducible."""
    ordered = sorted(puzzles, key=lambda puzzle: puzzle.id)
    chosen: dict[str, Puzzle] = {}
    for theme in THEME_ALLOWLIST:
        for bucket, _, _ in BUCKETS:
            taken = 0
            for puzzle in ordered:
                if taken >= per_theme_and_bucket:
                    break
                if puzzle.bucket != bucket or theme not in puzzle.themes or puzzle.id in chosen:
                    continue
                chosen[puzzle.id] = puzzle
                taken += 1
    return sorted(chosen.values(), key=lambda puzzle: puzzle.id)


def render_jsonl(puzzles: list[Puzzle]) -> str:
    return "".join(json.dumps(p.as_row(), ensure_ascii=False, sort_keys=True) + "\n" for p in puzzles)


def render_meta(source_version: str, source_hash: str, puzzles: list[Puzzle], jsonl: str) -> str:
    counts: dict[str, int] = {bucket: 0 for bucket, _, _ in BUCKETS}
    for puzzle in puzzles:
        counts[puzzle.bucket] += 1
    meta = {
        "source_url": SOURCE_URL,
        "source_version": source_version,
        "source_sha256": source_hash,
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "columns": list(COLUMNS),
        "theme_allowlist": list(THEME_ALLOWLIST),
        "rating_buckets": {bucket: [low, high] for bucket, low, high in BUCKETS},
        "max_solver_moves": MAX_SOLVER_MOVES,
        "puzzle_count": len(puzzles),
        "bucket_counts": counts,
        "output_hash": "sha256:" + hashlib.sha256(jsonl.encode("utf-8")).hexdigest(),
    }
    return json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def import_puzzles(
    source_csv: Path,
    source_version: str,
    output_dir: Path,
    per_theme_and_bucket: int = PER_THEME_AND_BUCKET,
) -> Path:
    if not SOURCE_VERSION_PATTERN.match(source_version):
        raise PuzzleImportError(f"source version must be a YYYY-MM snapshot, got {source_version!r}")
    puzzles = select(read_source(source_csv), per_theme_and_bucket)
    jsonl = render_jsonl(puzzles)
    source_hash = hash_file(source_csv)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "puzzles.jsonl"
    jsonl_path.write_text(jsonl, encoding="utf-8", newline="\n")
    (output_dir / "puzzles.meta.json").write_text(
        render_meta(source_version, source_hash, puzzles, jsonl), encoding="utf-8", newline="\n"
    )
    return jsonl_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, required=True, help="decompressed lichess_db_puzzle.csv")
    parser.add_argument("--source-version", required=True, help="YYYY-MM snapshot the csv was downloaded from")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--per-theme-and-bucket", type=int, default=PER_THEME_AND_BUCKET)
    args = parser.parse_args()
    path = import_puzzles(args.source_csv, args.source_version, args.output_dir, args.per_theme_and_bucket)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
