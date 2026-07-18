# Шахматы с Юрой

Voice-first chess for Yandex Alice, created for an experienced blind chess player.

The skill is designed to run a complete game without a screen, tolerate natural Russian chess phrases,
explain illegal moves, describe the current position, and play against a persistent Stockfish engine.
Screen-capable devices additionally receive an updated board image.

## Status

Architecture and MVP implementation are in progress. The active plans are:

- [Product plan](docs/plans/product/20260718-yura-chess-mvp.md)
- [Ralphex development plan](docs/plans/dev/20260718-yura-chess-mvp.md)

## Planned stack

- Python 3.12
- FastAPI and Pydantic
- python-chess and native Stockfish
- SQLite in WAL mode
- pytest, Ruff, and mypy
- Docker Compose on Firebat

## Development

```bash
uv sync --all-extras
uv run uvicorn yura_chess.main:app --reload
```

Health endpoints:

- `GET /health/live`
- `GET /health/ready`

The planned production webhook is `https://chess.waxim.ru/alice/webhook`.
