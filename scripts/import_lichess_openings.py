"""Convert the Lichess CC0 ECO tables into the compact opening set the skill ships.

The importer is offline on purpose: it reads a local checkout of
`lichess-org/chess-openings` pinned to one revision, and runtime never touches
the network. Running it twice on the same source must produce byte-identical
output, so nothing here depends on the wall clock, on dict ordering, or on the
order the source files happen to be read in.

Usage:

    uv run python scripts/import_lichess_openings.py \\
        --source-dir /path/to/chess-openings \\
        --source-revision <full git sha>
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

SOURCE_FILES = ("a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv")
SOURCE_REPOSITORY = "https://github.com/lichess-org/chess-openings"
LICENSE = "CC0-1.0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
ECO_PATTERN = re.compile(r"^[A-E][0-9]{2}$")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "yura_chess" / "data"
COLUMNS = ("eco", "opening", "variation", "uci")


class OpeningImportError(ValueError):
    """A source row that cannot be trusted; the importer writes nothing."""


@dataclass(frozen=True, slots=True)
class Opening:
    eco: str
    opening: str
    variation: str
    uci: str

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        # `uci` last is the total-order tiebreaker: two rows can share eco+name.
        return (self.eco, self.opening, self.variation, self.uci)


def parse_row(source: str, line: int, eco: str, name: str, pgn: str) -> Opening:
    where = f"{source}:{line}"
    if not ECO_PATTERN.match(eco):
        raise OpeningImportError(f"{where}: bad ECO code {eco!r}")
    opening, _, variation = name.partition(": ")
    if not opening.strip():
        raise OpeningImportError(f"{where}: empty opening name")
    return Opening(eco=eco, opening=opening.strip(), variation=variation.strip(), uci=uci_prefix(where, pgn))


def uci_prefix(where: str, pgn: str) -> str:
    """Replay the SAN line on a board so an illegal or malformed move is rejected here."""
    board = chess.Board()
    moves: list[str] = []
    for token in pgn.split():
        if token.endswith("."):
            continue
        try:
            move = board.parse_san(token)
        except ValueError as error:
            raise OpeningImportError(f"{where}: cannot play {token!r}: {error}") from error
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise OpeningImportError(f"{where}: empty move list")
    return " ".join(moves)


def read_source(source_dir: Path) -> list[Opening]:
    openings: list[Opening] = []
    for name in SOURCE_FILES:
        path = source_dir / name
        if not path.is_file():
            raise OpeningImportError(f"missing source file {path}")
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            header = next(reader, None)
            if header != ["eco", "name", "pgn"]:
                raise OpeningImportError(f"{name}: unexpected header {header!r}")
            for line, row in enumerate(reader, start=2):
                if not row:
                    continue
                if len(row) != 3:
                    raise OpeningImportError(f"{name}:{line}: expected 3 columns, got {len(row)}")
                openings.append(parse_row(name, line, *row))
    if not openings:
        raise OpeningImportError(f"no openings found in {source_dir}")
    return openings


def render_tsv(openings: list[Opening]) -> str:
    lines = ["\t".join(COLUMNS)]
    lines.extend("\t".join((o.eco, o.opening, o.variation, o.uci)) for o in sorted(openings, key=lambda o: o.sort_key))
    return "\n".join(lines) + "\n"


def render_meta(source_revision: str, openings: list[Opening], tsv: str) -> str:
    meta = {
        "source_repository": SOURCE_REPOSITORY,
        "source_revision": source_revision,
        "source_files": list(SOURCE_FILES),
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "columns": list(COLUMNS),
        "opening_count": len(openings),
        "output_hash": "sha256:" + hashlib.sha256(tsv.encode("utf-8")).hexdigest(),
    }
    return json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def import_openings(source_dir: Path, source_revision: str, output_dir: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise OpeningImportError(f"source revision must be a full git sha, got {source_revision!r}")
    openings = read_source(source_dir)
    tsv = render_tsv(openings)
    output_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = output_dir / "openings.tsv"
    tsv_path.write_text(tsv, encoding="utf-8", newline="\n")
    (output_dir / "openings.meta.json").write_text(
        render_meta(source_revision, openings, tsv), encoding="utf-8", newline="\n"
    )
    return tsv_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="local checkout of lichess-org/chess-openings")
    parser.add_argument("--source-revision", required=True, help="full git sha the checkout is pinned to")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    path = import_openings(args.source_dir, args.source_revision, args.output_dir)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
