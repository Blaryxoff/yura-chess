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
- MariaDB 11.4 with SQLAlchemy 2 and Alembic
- pytest, Ruff, and mypy
- Docker Compose on Firebat

## Development

```bash
cp .env.example .env
docker compose up -d mariadb
uv sync --all-extras
uv run alembic upgrade head
uv run uvicorn --factory yura_chess.main:create_app --reload
```

Health endpoints:

- `GET /health/live` — process liveness, independent of the database
- `GET /health/ready` — returns 503 until the database connection and schema check pass

### Tests

Repository tests run against a real MariaDB 11.4 and are skipped without a DSN:

```bash
docker exec -i yura-chess-mariadb-1 mariadb -uroot -proot_dev \
  -e "CREATE DATABASE IF NOT EXISTS yura_chess_test CHARACTER SET utf8mb4"
YURA_CHESS_TEST_DATABASE_URL='mysql+pymysql://root:root_dev@127.0.0.1:3307/yura_chess_test?charset=utf8mb4' \
  uv run pytest
```

The test fixtures build the schema by running the Alembic migrations, so every run exercises them.

The planned production webhook is `https://chess.waxim.ru/alice/webhook`.
