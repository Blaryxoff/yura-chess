# Third-party notices

«Шахматы с Юрой» distributes its own source under GPL-3.0-or-later (see
[`LICENSE`](LICENSE)). The published container image
`ghcr.io/blaryxoff/yura-chess` additionally contains the third-party works below,
each under its own terms.

## python-chess

- Package: `chess` (python-chess), imported in-process throughout `src/yura_chess/`.
- License: GPL-3.0-or-later.
- Source: <https://github.com/niklasf/python-chess>.
- The image ships the installed virtualenv, so every image is a distribution of
  this library alongside the application. Its license is the reason the project
  itself is GPL-3.0-or-later.

## Stockfish

- Invoked as a separate process over UCI; never linked into the application and
  never vendored into this repository.
- License: GPL-3.0-or-later.
- Installed from the Debian `stockfish` package in the runtime image, which makes
  the image a redistribution. Corresponding source is Debian's:
  <https://sources.debian.org/src/stockfish/>. Upstream:
  <https://github.com/official-stockfish/Stockfish>.

## Lichess opening catalogue

- File: `src/yura_chess/data/openings.tsv`.
- License: CC0-1.0.
- Source: <https://github.com/lichess-org/chess-openings>; the exact revision and
  a deterministic hash of the import are recorded in
  `src/yura_chess/data/openings.meta.json`.

## Lichess puzzle set

- File: `src/yura_chess/data/puzzles.jsonl`.
- License: CC0-1.0.
- Source: the public lichess puzzle database; source checksum, rating buckets and
  a deterministic hash of the import are recorded in
  `src/yura_chess/data/puzzles.meta.json`.

## Sound cues

- Files: `artifacts/sounds/`.
- Licenses: CC0-1.0, and CC-BY-4.0 where the source table says so.
- Per-recording authors, Freesound links and the applied processing are listed in
  [`artifacts/sounds/SOURCES.md`](artifacts/sounds/SOURCES.md). CC-BY attribution
  must be preserved wherever these files are redistributed.

## Python dependencies

The remaining runtime dependencies are permissively licensed (MIT, BSD-3-Clause,
MPL-2.0). The authoritative list, with resolved versions, is `uv.lock`.
