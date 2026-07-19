# Шахматы с Юрой

Voice-first chess for Yandex Alice, created for an experienced blind chess player.

The skill is designed to run a complete game without a screen, tolerate natural Russian chess phrases,
explain illegal moves, describe the current position, and play against a persistent Stockfish engine.
Screen-capable devices additionally receive an updated board image.

## Status

The MVP is implemented and deployed to production. The skill is being prepared
for public Yandex moderation; voice puzzles remain a post-release milestone.
The completed plans are:

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

### Screen board lifecycle

Board PNGs are rendered in memory and uploaded to Yandex Dialogs only for
screen-capable requests. Identical positions reuse the same `image_id`. A
quota-aware TTL/LRU maintenance pass deletes remote images before removing their
MariaDB mappings, keeps a bounded 2,000-image working set with a 500-image burst,
and stops new uploads at 80% of the account quota. If an evicted position is
requested again, it is rendered and uploaded again. Any image API or cleanup
failure falls back to the complete voice response and never breaks the game.

### Tests

Repository tests run against a real MariaDB 11.4 and are skipped without a DSN:

```bash
docker exec -i yura-chess-mariadb-1 mariadb -uroot -proot_dev \
  -e "CREATE DATABASE IF NOT EXISTS yura_chess_test CHARACTER SET utf8mb4"
YURA_CHESS_TEST_DATABASE_URL='mysql+pymysql://root:root_dev@127.0.0.1:3307/yura_chess_test?charset=utf8mb4' \
  uv run pytest
```

The test fixtures build the schema by running the Alembic migrations, so every run exercises them.

## Shell testing without Alice

The shell runner uses the same command router, game service, speech composer,
MariaDB state, and Stockfish pool as the Alice webhook:

```bash
uv run yura-chess-shell --show-board --show-fen --orientation player
```

Run a command sequence without an interactive prompt:

```bash
uv run yura-chess-shell \
  --command "пешка е два е четыре" \
  --command "что на е четыре" \
  --command "какая позиция"
```

`--script path/to/commands.txt` reads one command per line and ignores blank
lines and comments beginning with `#`. The runner requires the same
`YURA_CHESS_DATABASE_URL` and `YURA_CHESS_IDENTITY_SALT` environment variables
as the web application. `--show-board` prints a coordinate-labelled Unicode
board after every game response; `--show-fen` adds the canonical FEN.
`--orientation player` follows the selected game color; `white` and `black`
force a side for debugging. Every shell process starts with the same empty
new-session request Alice sends, so a persistent `--profile` can test saved-game
resume and the last-two-moves reminder.

The planned production webhook is `https://chess.waxim.ru/alice/webhook`.

Product copy for the Yandex Dialogs listing is in
[`docs/yandex-skill-description.md`](docs/yandex-skill-description.md). The
post-release voice-puzzle milestone is outlined in
[`docs/product/puzzles-roadmap.md`](docs/product/puzzles-roadmap.md).
