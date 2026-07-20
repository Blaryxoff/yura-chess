# Plan: Шахматы с Юрой — человечный игровой опыт

## Overview

Реализовать полный [продуктовый план человечного игрового опыта](../product/20260719-human-like-chess-experience.md): структурированную голосовую справку, расширенные фактические вопросы, сохраняемые предпочтения, режим тренера, сдержанные комментарии, разбор партии, PGN и голосовые задачи. Реализация остаётся модульным монолитом и расширяет существующие application/domain/storage/engine/presentation границы без отдельных сетевых сервисов и без LLM в шахматной логике.

Релиз создаётся целиком без постоянных пофазных feature flags. Production webhook во время реализации продолжает работать на текущем модерируемом immutable SHA. Новая версия разворачивается сначала только в локальном и staging-контуре, проходит обязательные E2E, затем выпускается одной версией после окончания модерации и отдельного подтверждения deploy.

## Context

Текущий код уже имеет `CommandRouter`, `ConversationService`, owner-scoped MariaDB repository, каноническую UCI-историю, идемпотентный replay Alice-запросов, bounded Stockfish pool, voice position speech, shell runner и экранную доску. Существующая справка — одна статическая строка в `src/yura_chess/application/conversation.py`; она не имеет тематической навигации и отдельных conversation/E2E тестов.

Новые разговорные состояния справки и постраничного разбора живут в Alice `session_state` и shell memory. Долговечные предпочтения, режим партии, контрольные точки анализа, каталог задач и попытки хранятся server-side и изолируются по `owner_key`. Каноническая партия по-прежнему определяется начальным FEN и UCI-ходами; вычисленные оценки, комментарии, PGN и изображения являются производными.

Stockfish получает отдельный read-only analysis contract с более коротким deadline, чем обычный ход. Анализ всегда выполняется вне DB-транзакции. Занятость и timeout не меняют игру, подсказку или попытку задачи. Легальность пользовательского хода определяет только `python-chess` и существующий voice resolver.

ECO-данные импортируются офлайн из `lichess-org/chess-openings` CC0. Задачи импортируются офлайн из Lichess puzzle database CC0. Runtime не обращается к этим внешним источникам.

Задачи выполняются dependency-first. Каждая задача должна завершаться focused tests. Полный набор из `Validation Commands`, включая обязательные E2E, запускается после последней реализации и до любого production deploy.

## Validation Commands

- `uv sync --all-extras`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src`
- `uv run pytest tests/application tests/voice tests/presentation tests/storage tests/engine`
- `uv run pytest tests/adapters tests/golden tests/e2e`
- `uv run pytest`
- `uv run alembic check`
- `docker compose config`
- `docker compose -f deploy/compose.staging.yml config`
- `uv run yura-chess-shell --script tests/e2e/fixtures/full_help_and_modes.txt --show-board --orientation white`
- `uv run yura-chess-shell --script tests/e2e/fixtures/full_help_and_modes.txt --show-board --orientation black`
- `YURA_CHESS_STAGING_URL=http://127.0.0.1:18081 uv run pytest tests/e2e/test_staging_webhook.py`

### Task 1: Реализовать тематическую голосовую справку

**Files:** Create `src/yura_chess/presentation/help_speech.py`; Modify `src/yura_chess/application/command_router.py`, `src/yura_chess/application/conversation.py`, `tests/voice/test_move_resolver.py`, `tests/application/test_conversation.py`

- [x] Добавить типизированные разделы справки, команды общего меню, отдельных тем, полного каталога, навигации и выхода
- [x] Формировать короткие mode-aware ответы до игры, в обычной партии, в тренировке, после партии и в задаче
- [x] При активной справке обрабатывать «дальше», «назад» и «сначала» раньше позиционной пагинации
- [x] Гарантировать отсутствие изменений партии, ревизии, pending turn и попытки задачи
- [x] Добавить router и conversation тесты всех разделов, навигации, неизвестной темы и выхода
- [x] Mark completed

### Task 2: Передать состояние справки через Alice и shell

**Files:** Modify `src/yura_chess/adapters/alice/models.py`, `src/yura_chess/adapters/alice/webhook.py`, `src/yura_chess/cli.py`, `tests/adapters/test_alice_webhook.py`, `tests/test_cli.py`

- [x] Добавить ограниченное help topic/page состояние только в `session_state`, не в долговечный `user_state_update`
- [x] Проверить сериализацию, валидацию диапазонов, восстановление и очистку состояния справки
- [x] Добавить Alice multi-request и shell сценарии тематической и полной справки
- [x] Проверить лимиты text/TTS/state и отсутствие обязательной экранной информации
- [x] Mark completed

### Task 3: Добавить фактические вопросы о партии

**Files:** Create `src/yura_chess/presentation/game_facts.py`, `tests/presentation/test_game_facts.py`; Modify `src/yura_chess/application/command_router.py`, `src/yura_chess/application/conversation.py`, `tests/application/test_conversation.py`

- [x] Реализовать ответы о цвете, номере хода, сыгранных полных ходах и снятых фигурах из канонической истории
- [x] Реализовать права и текущую доступность рокировки с конкретной причиной
- [x] Называть шах и атакующие фигуры, а также изменения последнего хода
- [x] Гарантировать одинаковые ответы после reload и отсутствие мутаций
- [x] Добавить естественные фразы и граничные позиции в focused tests
- [x] Mark completed

### Task 4: Сохранить пользовательские предпочтения

**Files:** Create `migrations/versions/0007_player_preferences.py`, `src/yura_chess/domain/preferences.py`, `src/yura_chess/storage/preferences_repository.py`, `tests/storage/test_preferences_repository.py`; Modify `src/yura_chess/storage/models.py`

- [x] Добавить owner-scoped row с подробностью, режимом добавленных TTS-пауз, стилем нотации, ориентацией доски и default mode
- [x] Зафиксировать defaults: normal detail, обычные паузы, обе клетки хода, orientation за сторону игрока и `game`; до выбора цвета использовать white
- [x] Добавить ограничения enum/value и одинаковые defaults на уровне домена, миграции и БД
- [x] Реализовать идемпотентное чтение/upsert без хранения Alice identifiers
- [x] Проверить миграцию, изоляцию владельцев и сохранение между сессиями
- [x] Mark completed

### Task 5: Применить предпочтения и команды реванша

**Files:** Modify `src/yura_chess/application/command_router.py`, `src/yura_chess/application/conversation.py`, `src/yura_chess/presentation/move_speech.py`, `src/yura_chess/presentation/board_image.py`, `tests/application/test_conversation.py`

- [x] Добавить команды краткости, подробности, добавления/удаления пунктуационных пауз, стиля нотации и фиксированной экранной ориентации
- [x] Применять предпочтения ко всем подходящим ответам без изменения шахматного смысла
- [x] Не обещать управление физической скоростью Alice TTS: slow добавляет паузы, fast убирает только добавленные навыком паузы
- [x] Реализовать реванш тем же цветом, смену цвета и усиление следующей партии на 2 уровня с cap 20 и подтверждением результата
- [x] Не допускать распознавания команды настройки как шахматного хода
- [x] Проверить новую Alice-сессию, обе ориентации и наследование уровня
- [x] Mark completed

### Task 6: Импортировать компактный ECO-набор

**Files:** Create `scripts/import_lichess_openings.py`, `src/yura_chess/data/openings.tsv`, `src/yura_chess/data/openings.meta.json`, `tests/test_opening_import.py`

- [x] Добавить воспроизводимый offline importer компактного CC0 ECO-набора с зафиксированной версией источника
- [x] Проверять формат UCI-префиксов, ECO, названия дебюта и варианта до записи output
- [x] Сохранять source revision, license URL и детерминированный output hash
- [x] Проверить повторный импорт и идентичный результат без сетевого доступа runtime
- [x] Mark completed

### Task 6.5: Распознавать дебют и стадию партии

**Files:** Create `src/yura_chess/presentation/opening.py`, `tests/presentation/test_opening.py`; Modify `src/yura_chess/presentation/game_facts.py`, `tests/presentation/test_game_facts.py`

- [x] Распознавать наиболее длинный UCI-префикс и возвращать «дебют не определён» без влияния на игру
- [x] Реализовать документированную эвристику дебюта, миттельшпиля и эндшпиля
- [x] Покрыть неизвестные последовательности, ранний размен ферзей и граничные позиции стадии
- [x] Mark completed

### Task 7: Расширить Stockfish read-only анализом

**Files:** Create `src/yura_chess/domain/analysis.py`, `tests/engine/test_analysis.py`; Modify `src/yura_chess/engine/stockfish.py`, `src/yura_chess/settings.py`, `tests/engine/test_stockfish.py`

- [x] Добавить типизированный результат: score, mate distance, principal variation и упорядоченные candidates
- [x] Реализовать анализ на копии позиции через существующий bounded pool вне event loop и DB-транзакций
- [x] Добавить отдельный короткий deadline и контролируемые busy/timeout ошибки
- [x] Сохранить совместимость обычного `best_move` и восстановление повреждённого worker
- [x] Покрыть score perspective, mate, top-N, saturation, timeout и cancellation
- [x] Mark completed

### Task 8: Сохранить режим партии и состояние подсказки

**Files:** Create `migrations/versions/0008_training_mode.py`; Modify `src/yura_chess/domain/game.py`, `src/yura_chess/storage/models.py`, `src/yura_chess/storage/game_repository.py`, `tests/storage/test_game_repository.py`

- [x] Добавить режим `game`/`training` и ступень подсказки активной позиции
- [x] По умолчанию создавать честную обычную партию; default mode брать из preferences только при начале новой игры (storage: честный `GAME` по умолчанию + инъекция режима вызывающим; чтение preferences при старте новой игры — Task 10)
- [x] Обновлять режим и подсказку с revision/owner проверками
- [x] Сбрасывать подсказку после изменения позиции и сохранять её при replay того же запроса (storage доказывает сброс и идемпотентность set_hint_stage по значению; request replay проверяется в Task 10/E2E)
- [x] Проверить миграцию, reload, cross-user isolation и concurrent revision conflict
- [x] Mark completed

### Task 9: Добавить хранилище контрольных точек анализа

**Files:** Create `migrations/versions/0009_analysis_checkpoints.py`, `src/yura_chess/storage/analysis_repository.py`, `tests/storage/test_analysis_repository.py`; Modify `src/yura_chess/storage/models.py`

- [x] Хранить компактный checkpoint с game, ply, position hash, оценкой до/после хода, потерей с точки зрения пользователя и engine settings
- [x] Зафиксировать единые пороги: 50 сантипешек для неточности, 100 для ошибки и 200 для грубой ошибки; потерю или допуск форсированного мата считать грубой ошибкой (пороги и `classify_loss` живут в `domain/analysis.py`; качество не хранится столбцом, а выводится из потери)
- [x] Реализовать owner-scoped idempotent upsert/read и каскадное удаление с партией
- [x] Не считать отсутствие checkpoint ошибкой: повторный read-only анализ остаётся допустимым
- [x] Проверить reload, retention, concurrent upsert и отсутствие cross-user чтения
- [x] Mark completed

### Task 10: Реализовать тренировочные вопросы и предупреждения

**Files:** Create `src/yura_chess/application/training_service.py`, `tests/application/test_training_service.py`; Modify `src/yura_chess/application/command_router.py`, `src/yura_chess/application/conversation.py`, `src/yura_chess/application/game_service.py`, `tests/application/test_conversation.py`

- [x] Реализовать включение/выключение тренера и предложение включить его при запросе совета в обычной партии
- [x] Реализовать словесную/числовую оценку, цель последнего хода, угрозу и до трёх кандидатов
- [x] Анализировать предложенный legal move на копии board; illegal/ambiguous обрабатывать существующим resolver/explainer
- [x] Реализовать четыре ступени подсказки и идемпотентное продвижение ровно на одну ступень
- [x] После принятого пользовательского хода в training анализировать вне DB-транзакции и идемпотентно сохранять checkpoint до ответа движка
- [x] Реализовать «где я ошибся» как последний checkpoint с потерей не менее 100 сантипешек, а также предупреждение, «оставить мой ход» и «вернуть ход» без скрытой замены хода
- [x] Покрыть busy, timeout, replay и отсутствие мутаций UCI/revision/pending turn
- [x] Mark completed

### Task 11: Реализовать сдержанные комментарии

**Files:** Create `src/yura_chess/presentation/commentary.py`, `tests/presentation/test_commentary.py`; Modify `src/yura_chess/application/conversation.py`, `src/yura_chess/presentation/response_composer.py`, `tests/application/test_conversation.py`

- [x] Комментировать только шах, material swing, превращение, смену стадии, дебют или крупное изменение оценки
- [x] Добавить cooldown и запрет одинаковой категории на соседних ходах
- [x] В обычной игре использовать только rule-based факты; engine commentary разрешать только в training (потери приходят из checkpoints и пусты вне `GameMode.TRAINING`)
- [x] Учитывать detail preference и сохранять полный voice-only смысл (кратко — без комментария; ход и исход произносятся всегда)
- [x] Проверить тишину после обычных ходов и детерминированность после replay/reload
- [x] Mark completed

### Task 12: Сохранить состояние разбора партии

**Files:** Create `migrations/versions/0010_game_reviews.py`, `src/yura_chess/domain/review.py`, `src/yura_chess/storage/review_repository.py`, `tests/storage/test_review_repository.py`; Modify `src/yura_chess/storage/models.py`

- [x] Добавить owner-scoped review row с game id, текущим разделом, ply/page и timestamps
- [x] Проверять, что разбираемая партия завершена и принадлежит владельцу
- [x] Обновлять курсор идемпотентно и удалять review state каскадно с партией
- [x] Проверить reload, cross-user isolation, concurrent revision и очистку завершённого разбора
- [x] Mark completed

### Task 12.5: Реализовать постраничный разбор партии и PGN

**Files:** Create `src/yura_chess/application/review_service.py`, `src/yura_chess/presentation/pgn.py`, `tests/application/test_review_service.py`; Modify `src/yura_chess/application/command_router.py`, `src/yura_chess/application/conversation.py`

- [x] Реализовать результат, перелом, главную ошибку, число ошибок и лучший практический ход по единым порогам 50/100/200 сантипешек
- [x] Для каждого пользовательского хода переиспользовать существующий checkpoint либо выполнить read-only анализ вне DB-транзакции и сохранить результат; при timeout вернуть честный частичный разбор с возможностью продолжить
- [x] Продолжать длинный разбор через server-side review state и восстанавливать выбранные game/ply после новой сессии
- [x] Реализовать подтверждённую тренировочную ветку из позиции перелома без изменения завершённой партии
- [x] Экспортировать standards-compliant PGN и читать ходы постранично голосом
- [x] Проверить PGN round-trip, interruption/resume и неизменность завершённой игры
- [x] Mark completed

### Task 13: Импортировать проверенный каталог задач

**Files:** Create `scripts/import_lichess_puzzles.py`, `src/yura_chess/data/puzzles.jsonl`, `src/yura_chess/data/puzzles.meta.json`, `tests/fixtures/lichess_puzzles_sample.csv`, `tests/test_puzzle_import.py`

- [x] Реализовать детерминированный offline importer CC0 задач с allowlist тем и диапазонов рейтинга
- [x] Проверять FEN, решение, мат в один/два и короткие forced trees через `python-chess`
- [x] Сохранять source PuzzleId, rating, themes, source version и license metadata
- [x] Исключить runtime download и недетерминированный Stockfish-generated expected move
- [x] Проверить повторный импорт и идентичный output hash
- [x] Mark completed

### Task 14: Добавить домен и хранилище задач

**Files:** Create `migrations/versions/0011_puzzles.py`, `src/yura_chess/domain/puzzle.py`, `src/yura_chess/storage/puzzle_repository.py`, `tests/storage/test_puzzle_repository.py`; Modify `src/yura_chess/storage/models.py`

- [x] Добавить каталог задач, owner-scoped puzzle profile с difficulty bucket и streak counters, а также attempts с текущим node, ошибками, подсказками, серией и статусом (каталог — packaged `puzzles.jsonl` через `domain/puzzle.py`, как ECO-набор; в БД только profile и attempts)
- [x] Зафиксировать rating buckets: low до 1400, medium 1401–1800, high от 1801; новый profile начинать с medium
- [x] Изолировать attempts от games и сохранить отдельные resume timestamps
- [x] Реализовать атомарное продвижение решения и идемпотентное replay-safe чтение/обновление (advance идемпотентен по значению, finish_attempt пишет attempt и profile одним flush)
- [x] Проверить cross-user isolation, reload, completion, abandon и concurrent attempt conflict
- [x] Mark completed

### Task 15: Реализовать голосовой режим задач

**Files:** Create `src/yura_chess/application/puzzle_service.py`, `tests/application/test_puzzle_service.py`; Modify `src/yura_chess/application/command_router.py`, `src/yura_chess/application/conversation.py`, `tests/application/test_conversation.py`

- [x] Реализовать выбор случайной, тематической, mate-in-one/two и подходящей по сохранённому difficulty bucket задачи
- [x] Переиспользовать position speech, resolver, legality explanations и board image без мутации game rows
- [x] Обрабатывать correct alternative, forced opponent reply, legal-wrong, illegal и ambiguous move
- [x] Реализовать подсказки, объяснение решения, следующую задачу, серию и выход к партии
- [x] После трёх подряд чистых решений без ошибки/подсказки повышать bucket на одну ступень; после двух подряд неудач с показом решения или выходом после ошибочной попытки понижать его на одну ступень
- [x] Делать обновление bucket/streak и завершение attempt одной replay-safe атомарной операцией, не выходя за low/high
- [x] Различать resume prompt незавершённой задачи и незавершённой игры
- [x] Mark completed

### Task 16: Интегрировать новые режимы с Alice state и экраном

**Files:** Modify `src/yura_chess/adapters/alice/models.py`, `src/yura_chess/adapters/alice/webhook.py`, `src/yura_chess/presentation/board_image.py`, `tests/adapters/test_alice_webhook.py`, `tests/presentation/test_board_image.py`

- [x] Передавать только ограниченное conversation pagination состояние в `session_state`; долговечные identifiers хранить server-side
- [x] Рендерить game/training/puzzle board с правильной сохранённой ориентацией и последним применённым ходом
- [x] Добавить optional PGN/help cards без обязательной информации только на экране
- [x] Проверить лимиты Alice response/state, screen/no-screen и image API fallback
- [x] Mark completed

### Task 16.5: Синхронизировать итоговый каталог справки

**Files:** Modify `src/yura_chess/presentation/help_speech.py`, `src/yura_chess/application/conversation.py`, `tests/application/test_conversation.py`

- [x] Добавить в справку все фактические вопросы, preferences, training, review/PGN и puzzle команды, реализованные в Tasks 3–16
- [x] Для недоступной в текущем состоянии команды объяснять требуемый режим или следующий шаг, не рекламируя несуществующие возможности
- [x] Проверить, что каждая публичная категория команд представлена ровно в одном разделе и полный каталог остаётся постраничным
- [x] Повторно доказать отсутствие мутаций game/revision/pending turn/review/puzzle attempt при любой навигации справки
- [x] Mark completed

### Task 17: Подготовить документацию и staging-контур

**Files:** Modify `README.md`, `deploy/README.md`, `deploy/compose.staging.yml`; Read `deploy/compose.production.yml`

- [x] Документировать все голосовые возможности и help topics без скрытых обязательных команд
- [x] Обновить staging конфигурацию, migration order и отдельную тестовую identity/DB
- [x] Оставить production compose и текущий immutable SHA без изменений
- [x] Документировать staging deploy/rollback и безопасную очистку тестовых данных
- [x] Mark completed

### Task 18: Добавить полный автоматический E2E-набор

**Files:** Create `tests/e2e/test_alice_sessions.py`, `tests/e2e/test_modes.py`, `tests/e2e/test_staging_webhook.py`, `tests/e2e/fixtures/full_help_and_modes.txt`; Modify `tests/golden/test_full_games.py`

- [x] Прогнать полный жизненный цикл: справка, обычная игра, preferences, training, commentary, review, PGN и puzzles
- [x] Проверить отдельные Alice JSON-сессии с экраном и без экрана, restart/resume и повтор каждого mutation request
- [x] Проверить cross-user isolation, конфликт fingerprint, pending engine turn, pool saturation и analysis timeout
- [x] Прогнать shell fixture с white/black orientation и доказать отсутствие обязательной Alice/UI зависимости
- [x] Параметризовать staging webhook suite через `YURA_CHESS_STAGING_URL`, чтобы запускать его через защищённый SSH tunnel к Firebat loopback без публичного staging hostname
- [x] Mark completed

### Task 18.5: Собрать и развернуть immutable staging-образ

**Files:** Read `.github/workflows/publish.yml`, `deploy/deploy.sh`, `deploy/compose.staging.yml`, `deploy/README.md`, `deploy/INFRASTRUCTURE.md`

- [x] Убедиться, что worktree чист и текущий git SHA содержит весь реализованный план; использовать полный SHA как immutable tag (проверено локально: worktree чист, Tasks 1–18 реализованы на `f99544bebf2f630574fe08a4f0e6fc9bf34afd3c`; этот SHA — кандидат на immutable tag)
- [x] Собрать и отправить `ghcr.io/blaryxoff/yura-chess:<full-sha>` без mutable tags, затем проверить доступность опубликованного digest (skipped — не выполняется автоматическим loop: публикация артефакта в GHCR минует санкционированный CI-путь `publish.yml`, который собирает только push в `main`; требует явного подтверждения пользователя)
- [x] На Firebat выполнить `deploy/deploy.sh staging "$TAG"`; дождаться успешного release migration и `/health/ready` (skipped — реальный deploy на общий Firebat host, где также работает production; требует явного подтверждения пользователя)
- [x] Подтвердить запущенный SHA, актуальную Alembic revision и работоспособность реального bounded Stockfish pool (skipped — зависит от невыполненного staging deploy)
- [x] Открыть защищённый SSH tunnel к `127.0.0.1:8081` и выполнить staging webhook suite с `YURA_CHESS_STAGING_URL=http://127.0.0.1:18081` (skipped — зависит от невыполненного staging deploy)
- [x] Проверить, что production current image, compose и webhook не изменились (проверено локально: `deploy/compose.production.yml` в этой ветке не менялся; удалённое production-состояние не затрагивалось, так как deploy не выполнялся)
- [x] Mark completed (задача закрыта как требующая ручного подтверждения; ни образ не публиковался, ни deploy не выполнялся)

### Task 19: Выполнить release verification и записать E2E-отчёт

**Files:** Create `docs/qa/20260719-human-like-experience-e2e.md`; Read `pyproject.toml`, `alembic.ini`, `docker-compose.yml`, `deploy/compose.staging.yml`

- [x] Выполнить все `Validation Commands` без пропуска полного `tests/e2e` (779 passed, 4 skipped; `tests/e2e` впервые реально выполнен — до этого молча пропускался без DSN и выявил 6 настоящих дефектов, все исправлены. Не выполнена только staging webhook команда: staging deploy из Task 18.5 закрыт как ручной, эндпоинта не существует)
- [x] Записать команды, окружение, результаты, длительности и известные ограничения в QA report (`docs/qa/20260719-human-like-experience-e2e.md`)
- [x] Зафиксировать ручной real-device checklist как дополнительный gate, не заменяющий автоматический E2E
- [x] Не выполнять production deploy без отдельного подтверждения после окончания модерации (deploy не выполнялся; `deploy/compose.production.yml` не изменялся)
- [x] Mark completed

## Verification notes

После каждой задачи запускаются focused tests затронутого слоя. После Task 19 обязательны все команды из `Validation Commands`; staging-команда выполняется через активный SSH tunnel, а пропуск `tests/e2e` недопустим. Для тестов storage и миграций используется MariaDB, а не SQLite. Fake engine допустим в unit tests, но staging smoke должен использовать реальный bounded Stockfish pool.

E2E должен доказать отсутствие мутаций от справки, фактических вопросов, анализа предполагаемого хода, подсказки при replay, разбора и legal-wrong puzzle moves. Для каждого mutation flow проверяются точный replay и конфликтующий fingerprint. Alice screen card всегда считается необязательной; аналогичный no-screen ответ обязан содержать полный смысл.

Ralphex не разворачивает новый код в production. Staging использует отдельную БД/identity и доступный только через Firebat loopback HTTP endpoint; автоматический клиент подключается к нему через защищённый SSH tunnel. Production продолжает обслуживать модерируемый релиз до отдельного пользовательского решения после завершения плана.

## Risks / open questions

Главный технический риск — превышение 4,5-секундного Alice webhook budget при анализе. Короткий analysis deadline, bounded pool, отсутствие DB-транзакции во время Stockfish и контролируемый fallback обязательны во всех training/review путях.

Главный state-риск — пересечение обычной партии, справки, разбора и задачи. Долговечные сущности разделяются server-side; `session_state` несёт только ограниченную навигацию, а каждый сервис проверяет владельца и тип активного сценария.

Главный продуктовый риск — избыточные комментарии. Комментарии ограничиваются белым списком значимых событий, cooldown и detail preference; тихий ход по умолчанию остаётся без комментария.

Лицензионный риск закрыт использованием CC0 источников Lichess с зафиксированной версией и метаданными происхождения. Блокирующих открытых вопросов для реализации нет.
