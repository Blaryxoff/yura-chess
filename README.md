# Шахматы с Юрой

Voice-first chess for Yandex Alice, created for an experienced blind chess player.

The skill is designed to run a complete game without a screen, tolerate natural Russian chess phrases,
explain illegal moves, describe the current position, and play against a persistent Stockfish engine.
Screen-capable devices additionally receive an updated board image.

## Status

The complete human-like experience release is deployed to production and is
awaiting public Yandex moderation. CI, the MariaDB integration suite, local shell
flows and an opt-in public-webhook smoke replace a separately maintained staging
environment.

The plans are:

- [MVP product plan](docs/plans/product/20260718-yura-chess-mvp.md)
- [MVP development plan](docs/plans/dev/20260718-yura-chess-mvp.md)
- [Human-like experience product plan](docs/plans/product/20260719-human-like-chess-experience.md)
- [Human-like experience development plan](docs/plans/dev/20260719-human-like-chess-experience.md)

## Voice capabilities

Everything is reachable by voice alone. The screen card is always optional: the
voice-only reply carries the complete meaning, and no command is hidden behind
the board image. Nothing has to be memorised either — «что ты умеешь» (also
«помощь», «справка», «как играть») opens the spoken catalogue, «дальше»,
«назад» and «сначала» page through it, «выйти из справки» closes it. Naming a
section («справка по задачам», or just «задачи» right after the menu) jumps
straight to it, and «все команды» reads the whole catalogue page by page. When a
command needs a mode the player is not in, help says what to do first instead of
advertising it as available.

The nine help topics, with representative commands:

| Topic | Commands |
| --- | --- |
| `ходы` | «пешка е два е четыре», «конь эф три», «е два е четыре», «отмени ход»; «да»/«нет» confirm a re-asked move |
| `позиция` | «какая позиция» (two ranks per page, «дальше» continues), «что на е четыре», «где белые слоны», «чей ход», «есть ли шах», «какой был последний ход», «что делали черные четыре хода назад» |
| `факты` | «за кого я играю», «какой сейчас ход», «сколько ходов мы сыграли», «какие фигуры съедены», «могу ли я сделать рокировку», «кто дает шах», «какой дебют», «какая стадия партии», «что изменил последний ход» |
| `партия` | «новая игра черными уровень десять», «продолжить последнюю партию», «предлагаю ничью», «сдаюсь», «какой уровень», «реванш другим цветом», «сыграем сложнее» |
| `настройки` | «говори кратко»/«говори подробно», «говори медленнее»/«говори быстрее», «короткая нотация»/«полная нотация», «доска всегда за белых»/«за черных»/«по моему цвету» |
| `тренер` | «включи режим тренера», «оцени позицию», «назови оценку числом», «чем ты угрожаешь», «какие ходы хорошие», «почему ты так сходила», «что будет, если я сыграю коня эф три», «подскажи», «где я ошибся», «оставить мой ход» |
| `разбор` | «разбери партию», «продолжить разбор», «где перелом», «главная ошибка», «сколько я ошибся», «продиктуй ходы», «покажи pgn», «сыграть эту позицию заново», «выйти из разбора» |
| `задачи` | «дай задачу», «задача на мат в один», «задача на вилку», «повтори задачу», «следующая задача», «покажи решение», «какая у меня серия», «вернуться к партии» |
| `речь` | «что ты услышала», «повтори медленно», «повтори координаты по буквам» |

Preferences (detail, added TTS pauses, notation style, board orientation and the
default mode of the next game) are stored per player and survive a new Alice
session. «Говори медленнее» adds pauses and «говори быстрее» removes only the
pauses the skill itself added — the physical speed of Alice's voice is not
controlled by the skill. Coach explanations, warnings and hints only exist in the
coach mode; an ordinary game stays an honest game and comments only on genuinely
notable events.

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
- `GET /` — public product page used by players, moderators and brand verification

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
roadmaps behind the voice puzzles and the human-like experience are
[`docs/product/puzzles-roadmap.md`](docs/product/puzzles-roadmap.md) and
[`docs/product/human-like-experience-roadmap.md`](docs/product/human-like-experience-roadmap.md).
