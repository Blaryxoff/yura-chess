# Release verification: человечный игровой опыт

Отчёт о выполнении `Validation Commands` из
[dev-плана](../plans/dev/20260719-human-like-chess-experience.md) (Task 19).

## Окружение

| Параметр | Значение |
|---|---|
| Дата прогона | 2026-07-20 |
| Git base повторного прогона | `d82de48635121ff12ecc8648855a21803366471c`, ветка `human-like-chess-experience` |
| Проверенное состояние кода | рабочее дерево после Task 20; итоговый immutable SHA фиксируется перед staging deploy |
| Worktree | содержит только изменения human-like-experience review |
| Хост | macOS 26.5.2, arm64 |
| Python | 3.12.10 (`uv`) |
| Ключевые библиотеки | python-chess 1.11.2, SQLAlchemy 2.0.51 |
| БД | MariaDB 11.4.12 в контейнере `yura-chess-mariadb-1`, `127.0.0.1:3307` |
| Тестовая БД | `yura_chess_test` (отдельная от dev-схемы) |
| Alembic head | `0013` |
| Stockfish | `/opt/homebrew/bin/stockfish` (unit-тесты используют fake engine) |

Переменные окружения прогона:

```bash
YURA_CHESS_TEST_DATABASE_URL='mysql+pymysql://root:root_dev@127.0.0.1:3307/yura_chess_test?charset=utf8mb4'
YURA_CHESS_IDENTITY_SALT=verification-salt          # только для alembic check и shell-прогона
YURA_CHESS_IMAGE=ghcr.io/blaryxoff/yura-chess:<sha> # только для docker compose config
```

## Результаты

| # | Команда | Результат | Длительность |
|---|---|---|---|
| 1 | `uv sync --all-extras` | ✅ 45 пакетов, изменений нет | <1 s |
| 2 | `uv run ruff check .` | ✅ All checks passed | 0.1 s |
| 3 | `uv run ruff format --check .` | ✅ 108 файлов отформатированы | <1 s |
| 4 | `uv run mypy src` | ✅ 51 файл, ошибок нет | 0.3 s |
| 5 | `uv run pytest tests/application tests/voice tests/presentation tests/storage tests/engine` | ✅ 747 passed, пропусков нет | 9.03 s |
| 6 | `uv run pytest tests/adapters tests/golden tests/e2e` | ✅ 82 passed, 4 staging-only skipped | 56.88 s |
| 7 | `uv run pytest` | ✅ **883 passed, 4 skipped** | 69.91 s |
| 8 | `uv run alembic check` / `alembic current` | ✅ schema matches models; `0013 (head)` | 0.6 s |
| 9 | `docker compose config` | ✅ валиден | <1 s |
| 10 | `docker compose -f deploy/compose.staging.yml config` | ✅ валиден при заданном `YURA_CHESS_IMAGE` | <1 s |
| 11 | `yura-chess-shell --script tests/e2e/fixtures/full_help_and_modes.txt --show-board --orientation white` | ✅ весь сценарий отвечен, Unicode-доска `a…h`, exit 0 | 4.93 s |
| 12 | то же с `--orientation black` | ✅ весь сценарий отвечен, Unicode-доска `h…a`, exit 0 | 3.91 s |
| 12a | Оба shell-сценария одновременно для одного owner | ✅ оба exit 0; first-write deadlock не повторился | 4.65 / 5.12 s |
| 12b | Расширенный shell-аудит команд | ✅ факты, история, уровень, illegal move, настройки, справка, confirmations и puzzle repeat | 1.73 s |
| 13 | `YURA_CHESS_STAGING_URL=... pytest tests/e2e/test_staging_webhook.py` | ⏭️ не выполнена — см. «Известные ограничения» | — |

## Дефекты, найденные этим прогоном

Ключевая находка: **до этого прогона `tests/e2e` ни разу не выполнялся**. Без
`YURA_CHESS_TEST_DATABASE_URL` весь набор молча пропускается (`19 skipped`),
поэтому Task 18 был закрыт, ни разу не запустив свои тесты. При первом реальном
запуске упало 6 тестов; все шесть — настоящие дефекты, исправленные здесь.

1. `tests/e2e/test_alice_sessions.py` — хелпер `stored_moves` читал несуществующий
   столбец `move_uci`; фактическое имя в `game_moves` — `uci`. Ошибка SQL валила
   4 теста до выполнения их проверок. Исправлен запрос.
2. `tests/e2e/test_modes.py` — тест ожидал `GameStatus.FINISHED`, тогда как
   сценарий `full_help_and_modes.txt` завершает партию командой «сдаюсь», то есть
   корректный терминальный статус — `RESIGNED`. Ожидание исправлено; смысл теста
   (разбор не меняет завершённую партию) сохранён: статус, режим и ходы проверяются
   после разбора.
3. `src/yura_chess/application/conversation.py` — при `game_id`, который владелец не
   может загрузить (чужой или удалённый), состояние сбрасывалось и любая команда
   падала в `_start`, создавая новую партию, хотя у игрока была своя активная.
   Изоляция при этом не нарушалась, но игрок молча терял свою партию. Теперь
   выполняется откат к последней активной партии владельца — так же, как это уже
   делал путь возобновления новой сессии.
4. Два одновременных shell-сеанса одного владельца выявили MariaDB deadlock 1213
   при первой записи `player_preferences`. Добавлен один точечный retry всей
   короткой транзакции в новой Session; тот же механизм защищает первую блокировку
   puzzle profile. Ошибки с другими кодами не повторяются, replay claim задачи
   после rollback создаётся заново и сохраняется ровно один раз.

## Известные ограничения

- **Staging webhook suite не выполнялась.** Task 18.5 закрыт как требующий ручного
  подтверждения: образ в GHCR не публиковался и `deploy/deploy.sh staging` не
  запускался, поэтому staging-эндпоинта и SSH-туннеля не существует. Команда
  остаётся обязательным ручным gate: после подтверждённого staging deploy открыть
  туннель к `127.0.0.1:8081` и выполнить
  `YURA_CHESS_STAGING_URL=http://127.0.0.1:18081 uv run pytest tests/e2e/test_staging_webhook.py`.
  Без неё реальный bounded Stockfish pool на staging не проверен — локальные
  unit-тесты используют fake engine.
- Прогон выполнен на macOS/arm64, тогда как production-образ — linux/amd64.
- Тестовая БД `yura_chess_test` пересоздаётся миграциями на каждый прогон; dev- и
  production-данные не затрагивались.

## Ручной real-device checklist

Дополнительный gate, **не заменяющий** автоматический E2E. Выполняется на реальной
колонке/приложении Алисы после staging deploy:

- [ ] Запуск навыка, «что ты умеешь», навигация «дальше»/«назад», выход из справки.
- [ ] Новая игра с уровнем, ход голосом, распознавание с первого раза.
- [ ] Фактические вопросы: чей ход, какая позиция, какие фигуры съедены.
- [ ] Предпочтения: «говори подробнее», «называй обе клетки» — сохраняются после
      закрытия и повторного открытия навыка.
- [ ] Тренер: включение, оценка позиции, четыре ступени подсказки, «где я ошибся».
- [ ] Разбор завершённой партии, PGN, постраничная диктовка ходов.
- [ ] Задачи: выдача, подсказка, показ решения, выход.
- [ ] Устройство без экрана: ответ сохраняет полный смысл без картинки.
- [ ] Устройство с экраном: доска в сохранённой ориентации, последний ход виден.
- [ ] Обрыв сессии и возобновление: партия и незавершённая задача различаются.

## Production deploy

Production deploy **не выполняется** этим прогоном и не разрешается им. Текущий
production продолжает работать на модерируемом immutable SHA; `deploy/compose.production.yml`
в этой ветке не изменялся. Выпуск новой версии — только после окончания модерации
и отдельного явного подтверждения пользователя.
