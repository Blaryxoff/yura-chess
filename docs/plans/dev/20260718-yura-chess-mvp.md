# Plan: Шахматы с Юрой — архитектура и MVP

## Overview

Создать с нуля голосовой шахматный навык для Яндекс Алисы с серверным состоянием, полным журналом ходов, устойчивым контекстным распознаванием речи, конкретными объяснениями невозможных ходов и экранной доской. Реализация представляет собой модульный монолит на Python/FastAPI, разворачиваемый на Firebat и использующий один постоянно работающий процесс Stockfish.

## Context

Новый репозиторий расположен в `/Users/blaryx/www/yura-chess`. Существующий `/Users/blaryx/www/chess` остаётся форком `axtrace/alisa_chess` и используется только как источник идей, публичных сценариев и отзывов до уточнения лицензии.

Публичное имя навыка — «Шахматы с Юрой». Production endpoint — `https://chess.waxim.ru/alice/webhook`. Firebat использует host nginx для TLS и SNI, Incus-контейнеры для приложений и loopback proxy-devices для передачи портов. Секреты хранятся только на Firebat.

Технологии: Python 3.12, FastAPI, Pydantic, `python-chess`, SQLite WAL, pytest, Ruff, mypy, uv и Docker Compose. Alice `user_state_update` содержит только `game_id`, ревизию и минимальные метаданные; каноническая история хранится на сервере как начальная FEN и полный список UCI-ходов.

Архитектурные границы: Alice adapter не содержит шахматной логики; `GameService` не знает формат webhook; `MoveResolver` сопоставляет нормализованную речь с легальными ходами; `IllegalMoveExplainer` работает только после неуспешного легального сопоставления; Stockfish не участвует в проверке пользовательского хода.

## Validation Commands

- `uv sync --all-extras`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src`
- `uv run pytest`
- `docker compose config`

### Task 1: Инициализировать репозиторий и базовый HTTP runtime

**Files:** Create `pyproject.toml`, `README.md`, `.gitignore`, `.env.example`, `src/yura_chess/__init__.py`, `src/yura_chess/main.py`, `src/yura_chess/settings.py`, `tests/test_health.py`; Modify `AGENTS.md`, `.devkit/toolkit.json`

- [x] Настроить пакет `yura-chess` для Python 3.12 и группы runtime/dev зависимостей через uv
- [x] Создать `FastAPI` application factory и endpoints `/health/live`, `/health/ready`
- [x] Добавить типизированные настройки без значений секретов по умолчанию
- [x] Зафиксировать политику автоматического запуска тестов в `AGENTS.md`
- [x] Подключить `devkit-core` и сгенерировать project adapters для Codex, Claude и Cursor
- [x] Добавить focused health tests
- [x] Mark completed

### Task 2: Создать каноническую модель партии и SQLite repository

**Files:** Create `src/yura_chess/domain/game.py`, `src/yura_chess/storage/schema.sql`, `src/yura_chess/storage/game_repository.py`, `tests/storage/test_game_repository.py`

- [ ] Описать неизменяемые идентификаторы, статус, цвет игрока, настройки движка и ревизию партии
- [ ] Хранить начальную FEN и полный упорядоченный список UCI-ходов
- [ ] Восстанавливать `chess.Board` только через повторное применение истории
- [ ] Настроить SQLite WAL, foreign keys, транзакции и optimistic revision check
- [ ] Хранить обработанные Alice request IDs и сериализованные ответы для идемпотентного replay
- [ ] Покрыть создание, загрузку, конкурентную ревизию и replay unit-тестами
- [ ] Mark completed

### Task 3: Реализовать постоянный StockfishService

**Files:** Create `src/yura_chess/engine/stockfish.py`, `tests/engine/test_stockfish.py`; Modify `src/yura_chess/main.py`, `src/yura_chess/settings.py`

- [ ] Запускать один UCI-процесс в lifespan FastAPI и гарантированно закрывать его при shutdown
- [ ] Сериализовать вызовы движка через lock и не блокировать event loop
- [ ] Настроить `Threads`, `Hash`, уровень силы и жёсткий лимит времени из settings
- [ ] Возвращать безопасную ошибку при отсутствии бинаря, timeout или завершении процесса
- [ ] Включить проверку процесса в `/health/ready`, не запуская поиск хода
- [ ] Использовать fake engine в unit-тестах без реального бинаря
- [ ] Mark completed

### Task 4: Реализовать GameService и шахматный жизненный цикл

**Files:** Create `src/yura_chess/application/game_service.py`, `src/yura_chess/domain/results.py`, `tests/application/test_game_service.py`

- [ ] Реализовать start/continue/player move/engine move/resign/new game/undo turn
- [ ] Применять пользовательский ход и ответ Stockfish в одной транзакционной операции партии
- [ ] Проверять мат, пат, недостаточный материал, 75 ходов и пятикратное повторение автоматически
- [ ] Поддержать требование ничьей по 50 ходам и троекратному повторению отдельной командой
- [ ] Отменять полный ход пользователя и движка на основе UCI-истории
- [ ] Покрыть многошаговые сценарии с восстановлением board между отдельными вызовами
- [ ] Mark completed

### Task 5: Реализовать Alice protocol adapter и idempotency

**Files:** Create `src/yura_chess/adapters/alice/models.py`, `src/yura_chess/adapters/alice/webhook.py`, `src/yura_chess/application/player_identity.py`, `tests/adapters/test_alice_webhook.py`; Modify `src/yura_chess/main.py`

- [ ] Валидировать обязательные поля протокола Алисы без логирования персональных payload целиком
- [ ] Сопоставлять авторизованный `user_id` с активной партией и использовать `application_id` как ограниченный fallback
- [ ] Возвращать минимальный `user_state_update` с `game_id` и ревизией
- [ ] При повторном `message_id` возвращать сохранённый ответ без повторного выполнения команды
- [ ] Определять поддержку экрана через `meta.interfaces.screen`
- [ ] Ограничить размер `text`, `tts` и state до лимитов платформы
- [ ] Добавить golden tests для последовательности отдельных Alice requests
- [ ] Mark completed

### Task 6: Реализовать CommandRouter и голосовое распознавание ходов

**Files:** Create `src/yura_chess/application/command_router.py`, `src/yura_chess/voice/normalizer.py`, `src/yura_chess/voice/move_resolver.py`, `src/yura_chess/voice/types.py`, `tests/voice/test_move_resolver.py`

- [ ] Отделить команды управления, запросы позиции и шахматные ходы до изменения партии
- [ ] Нормализовать русские названия фигур, вертикали, числа, служебные слова, взятия, рокировку и превращение
- [ ] Для текущей позиции генерировать допустимые голосовые формы каждого легального хода
- [ ] Возвращать `resolved`, `ambiguous` или `unmatched` с измеримой уверенностью
- [ ] Никогда не выбирать первый ход из нескольких совпадений
- [ ] Добавить команду «что ты услышала» и состояние ожидаемого уточнения
- [ ] Построить параметризованные тесты на синтетических и реальных ASR-транскриптах
- [ ] Mark completed

### Task 7: Добавить объяснение невозможных ходов

**Files:** Create `src/yura_chess/voice/illegal_move.py`, `tests/voice/test_illegal_move.py`; Modify `src/yura_chess/application/command_router.py`

- [ ] Сохранять распознанные piece/source/destination даже при отсутствии легального совпадения
- [ ] Объяснять пустое исходное поле, чужой цвет и занятое своей фигурой поле назначения
- [ ] Объяснять геометрию фигуры и первую блокирующую клетку для ладьи, слона и ферзя
- [ ] Объяснять оставление короля под шахом и ход, не устраняющий текущий шах
- [ ] Объяснять правила пешки, превращения, взятия на проходе и рокировки
- [ ] Использовать общий fallback только для недостаточно определённого намерения
- [ ] Mark completed

### Task 8: Реализовать доступное описание позиции и TTS

**Files:** Create `src/yura_chess/presentation/position_speech.py`, `src/yura_chess/presentation/move_speech.py`, `src/yura_chess/presentation/response_composer.py`, `tests/presentation/test_speech.py`

- [ ] Позволить спросить содержимое поля, расположение типа фигур, фигуры одной стороны и всю позицию
- [ ] Выдавать полную позицию стабильными группами с продолжением командой «дальше»
- [ ] Формировать отдельный `tts` только когда произношение отличается от display text
- [ ] Однозначно озвучивать файлы, ранги, взятия, шах, мат, рокировку и превращение
- [ ] Добавить медленный повтор координат без изменения состояния партии
- [ ] Проверить каждый ответ без обязательной экранной информации
- [ ] Mark completed

### Task 9: Добавить экранную доску и Yandex image cache

**Files:** Create `src/yura_chess/presentation/board_image.py`, `src/yura_chess/adapters/yandex_images.py`, `tests/presentation/test_board_image.py`; Modify `src/yura_chess/presentation/response_composer.py`, `src/yura_chess/storage/schema.sql`

- [ ] Рендерить PNG в памяти по текущему board, цвету пользователя и последнему ходу
- [ ] Вычислять стабильный position hash без записи PNG на постоянный диск
- [ ] Загружать изображение в Yandex resources и сохранять `position_hash -> image_id`
- [ ] Возвращать `BigImage` только для screen-capable request
- [ ] Реализовать ограниченную очистку image IDs по TTL/LRU после проверки квот API
- [ ] Проверить fallback `text` и `tts` на voice-only request
- [ ] Mark completed

### Task 10: Подготовить Firebat deployment и релизный контур

**Files:** Create `Dockerfile`, `docker-compose.yml`, `deploy/nginx/chess.waxim.ru.conf`, `deploy/README.md`, `.github/workflows/ci.yml`, `tests/golden/test_full_games.py`

- [ ] Собрать non-root image с Stockfish и read-only application filesystem
- [ ] Смонтировать отдельные volumes для SQLite и ограниченного runtime cache
- [ ] Добавить health checks, resource limits, restart policy и ротацию логов
- [ ] Публиковать приложение только на container loopback и описать Incus proxy-device до host loopback
- [ ] Настроить публичный `chess.waxim.ru` без basic-auth, с TLS, body/time limits и rate limiting
- [ ] Добавить CI lint/type/test/Compose validation и запрет секретов в репозитории
- [ ] Провести двадцать golden games, voice-only QA и screen-device QA до публичной модерации
- [ ] Mark completed

## Verification notes

Каждая задача выполняется отдельным логическим изменением. Сначала запускаются focused tests изменённого слоя, затем все команды из `Validation Commands`. Реальный Stockfish используется только в smoke/integration проверке; unit suite не зависит от установленного бинаря.

Alice webhook проверяется на повторную доставку, отсутствие авторизованного `user_id`, повреждённый state, слишком длинную реплику, параллельные запросы одной партии и timeout Stockfish. Позиция до и после любой ошибки должна оставаться доказуемо согласованной с UCI-историей.

Перед Firebat cutover выполняются `docker compose config`, локальный health smoke, восстановление SQLite из backup и внешний HTTPS-запрос через nginx. Production секреты и реальные `.env` не попадают в GitHub.

## Risks / open questions

Главный первый риск — повтор webhook после timeout, когда пользовательский ход уже записан, а ответ Stockfish ещё вычисляется. Идемпотентность, транзакционная модель turn и сохранённый response должны быть реализованы до реального теста Алисы.

Ошибки ASR происходят до webhook и не могут быть полностью устранены кодом навыка. Компенсация строится на контексте легальных ходов, реальном корпусе транскриптов и безопасном переспросе без изменения доски.

Yandex image API имеет квоты и требует проверки жизненного цикла загруженных изображений. Экранная карточка не блокирует голосовой MVP; при недоступности image API навык возвращает полноценный voice/text ответ.

Firebat является домашним failure domain. SQLite backup, health monitoring и понятный fallback при недоступности движка обязательны до публичного релиза. Блокирующих открытых продуктовых или архитектурных вопросов нет.
