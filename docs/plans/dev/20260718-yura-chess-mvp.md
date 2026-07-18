# Plan: Шахматы с Юрой — архитектура и MVP

## Overview

Реализовать [продуктовый план MVP](../product/20260718-yura-chess-mvp.md): публичный голосовой шахматный навык для Яндекс Алисы с серверным состоянием, изоляцией партий разных пользователей, полным журналом ходов, устойчивым контекстным распознаванием речи, конкретными объяснениями невозможных ходов и экранной доской. Реализация представляет собой модульный монолит на Python/FastAPI, разворачиваемый на Firebat, использующий MariaDB и ограниченный пул постоянно работающих процессов Stockfish.

## Context

Новый репозиторий расположен в `/Users/blaryx/www/yura-chess`. Существующий `/Users/blaryx/www/chess` остаётся форком `axtrace/alisa_chess` и используется только как источник идей, публичных сценариев и отзывов до уточнения лицензии.

Публичное имя навыка — «Шахматы с Юрой». Production endpoint — `https://chess.waxim.ru/alice/webhook`. Firebat использует host nginx для TLS и SNI, Incus-контейнеры для приложений и loopback proxy-devices для передачи портов. Секреты хранятся только на Firebat.

Технологии: Python 3.12, FastAPI, Pydantic, `python-chess`, SQLAlchemy 2, Alembic, MariaDB 11.4, pytest, Ruff, mypy, uv и Docker Compose. Alice `user_state_update` содержит только `game_id`, ревизию и минимальные метаданные; каноническая история хранится на сервере как начальная FEN и полный список UCI-ходов.

Публичный сервис изолирует данные по псевдонимизированному владельцу: каждый доступ к партии проверяет владельца независимо от переданного `game_id`. Идемпотентность webhook определяется составным ключом `(skill_id, session_id, message_id)` и fingerprint значимых полей запроса. Повтор с тем же fingerprint получает сохранённый ответ или продолжает сохранённый `pending_engine_turn`; совпавший ключ с другим fingerprint отклоняется без изменения партии.

Пул Stockfish ограничен настройкой и по умолчанию содержит два независимых worker-процесса, каждый со своим lock. Получение worker имеет короткий timeout, поиск хода — жёсткий deadline не более 3 секунд, а весь webhook — бюджет 4,5 секунды. Исчерпание пула или timeout возвращают контролируемый ответ и не оставляют позицию в неопределённом состоянии.

Обработка хода разделена на две короткие DB-транзакции. Транзакция A проверяет владельца, revision и идемпотентность, применяет ход пользователя и фиксирует `pending_engine_turn`. Stockfish вычисляет ответ вне DB-транзакции. Транзакция B повторно проверяет revision, применяет ответ движка и сохраняет финальный Alice response. Retry возобновляет или воспроизводит pending turn без повторного хода пользователя.

Архитектурные границы: Alice adapter не содержит шахматной логики; `GameService` не знает формат webhook; `MoveResolver` сопоставляет нормализованную речь с легальными ходами; `IllegalMoveExplainer` работает только после неуспешного легального сопоставления; Stockfish не участвует в проверке пользовательского хода. Текущие bootstrap-описания движка и хранилища в `AGENTS.md`/`README.md` устарели и обновляются соответствующими задачами этого плана.

Firebat test использует существующий `staging-mariadb` в общем Incus-контейнере `staging` с отдельной базой и отдельным пользователем `yura-chess`; его фактическая версия проверяется на стенде и покрывается compatibility-тестом. Production работает в выделенном Incus-стеке `yura-chess` со своей MariaDB 11.4; MariaDB не публикуется наружу. Для обеих сред обязательны health checks, миграции, резервное копирование и проверяемое восстановление.

## Validation Commands

- `uv sync --all-extras`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src`
- `uv run pytest`
- `[ ! -f alembic.ini ] || uv run alembic check`
- `[ ! -f docker-compose.yml ] || docker compose config`

### Task 1: Инициализировать репозиторий и базовый HTTP runtime

**Files:** Create `pyproject.toml`, `README.md`, `.gitignore`, `.env.example`, `src/yura_chess/__init__.py`, `src/yura_chess/main.py`, `src/yura_chess/settings.py`, `tests/test_health.py`; Modify `AGENTS.md`, `.devkit/toolkit.json`

- [x] Настроить пакет `yura-chess` для Python 3.12 и группы runtime/dev зависимостей через uv
- [x] Создать `FastAPI` application factory и endpoints `/health/live`, `/health/ready`
- [x] Добавить типизированные настройки без значений секретов по умолчанию
- [x] Зафиксировать политику автоматического запуска тестов в `AGENTS.md`
- [x] Подключить `devkit-core` и сгенерировать project adapters для Codex, Claude и Cursor
- [x] Добавить focused health tests
- [x] Mark completed

### Task 2: Создать каноническую модель партии и MariaDB repository

**Files:** Create `alembic.ini`, `docker-compose.yml`, `migrations/env.py`, `migrations/versions/0001_games_and_requests.py`, `src/yura_chess/domain/game.py`, `src/yura_chess/storage/database.py`, `src/yura_chess/storage/models.py`, `src/yura_chess/storage/game_repository.py`, `tests/storage/test_game_repository.py`; Modify `pyproject.toml`, `.env.example`, `README.md`, `src/yura_chess/settings.py`, `src/yura_chess/main.py`, `tests/test_health.py`

- [x] Подключить SQLAlchemy 2 и Alembic к MariaDB 11.4 без отдельного fallback-хранилища
- [x] Описать модели партии, упорядоченной UCI-истории, незавершённого engine turn и replay-запроса
- [x] Хранить неизменяемый идентификатор и псевдонимизированного владельца, статус, цвет игрока, настройки движка и optimistic revision партии
- [x] Восстанавливать `chess.Board` только через повторное применение полной UCI-истории от начальной FEN
- [x] Создать Alembic migration с foreign keys, уникальным составным replay key `(skill_id, session_id, message_id)` и request fingerprint
- [x] Обеспечить короткие транзакции, revision check и запрет загрузки или изменения партии без совпадения владельца
- [x] Добавить локальный MariaDB service и health check в Compose; тесты repository выполнять против MariaDB, а не in-memory замены
- [x] Заменить bootstrap-настройку файловой БД на типизированный MariaDB DSN и включить проверку соединения и схемы в `/health/ready`
- [x] Покрыть создание, загрузку, межпользовательскую изоляцию, конкурентную ревизию, конфликт fingerprint и replay integration-тестами
- [x] Mark completed

### Task 3: Реализовать ограниченный пул Stockfish

**Files:** Create `src/yura_chess/engine/stockfish.py`, `tests/engine/test_stockfish.py`; Modify `src/yura_chess/main.py`, `src/yura_chess/settings.py`, `AGENTS.md`

- [x] Запускать в lifespan FastAPI настраиваемый bounded pool, по умолчанию из двух независимых UCI-процессов, и гарантированно закрывать все процессы при shutdown
- [x] Выделить каждому worker отдельный lock и выполнять блокирующие UCI-вызовы вне event loop
- [x] Настроить `Threads`, `Hash`, уровень силы, acquisition timeout и deadline поиска не более 3 секунд из settings
- [x] При исчерпании пула быстро возвращать контролируемый результат, не создавать неограниченную очередь и укладываться в общий Alice deadline 4,5 секунды
- [x] Перезапускать только повреждённый worker после отсутствия бинаря, timeout или завершения процесса и не отдавать его следующему запросу до readiness
- [x] Включить количество готовых workers в `/health/ready`, не запуская поиск хода
- [x] Использовать fake workers в unit-тестах; проверить параллельную работу двух users, насыщение пула, timeout и shutdown
- [x] Обновить архитектурное правило `AGENTS.md` с одного процесса на утверждённый bounded pool
- [x] Mark completed

### Task 4: Реализовать GameService и устойчивый шахматный жизненный цикл

**Files:** Create `src/yura_chess/application/game_service.py`, `src/yura_chess/domain/results.py`, `tests/application/test_game_service.py`; Modify `src/yura_chess/storage/game_repository.py`

- [x] Реализовать start/continue/player move/engine move/resign/new game/undo turn с обязательной проверкой владельца
- [x] В транзакции A проверять owner/revision/replay, применять ход пользователя и фиксировать `pending_engine_turn`
- [x] Вызывать Stockfish после commit транзакции A и без открытой DB-транзакции
- [x] В транзакции B повторно проверять owner/revision/pending token, применять ответ движка и атомарно сохранять финальный response
- [x] При acquisition timeout или search timeout сохранять согласованный pending turn и возвращать ответ, который предлагает продолжить без повторения шахматного хода
- [x] При retry с тем же replay key возобновлять pending engine calculation либо возвращать готовый response; никогда не применять ход пользователя дважды
- [x] Проверять мат, пат, недостаточный материал, 75 ходов и пятикратное повторение автоматически
- [x] Поддержать требование ничьей по 50 ходам и троекратному повторению отдельной командой
- [x] Отменять полный завершённый ход пользователя и движка на основе UCI-истории; явно отклонять undo во время pending turn
- [x] Покрыть восстановление board, crash/retry между транзакциями A/B, конфликт revision и независимые параллельные партии
- [x] Mark completed

### Task 5: Реализовать Alice protocol adapter, identity и idempotency

**Files:** Create `src/yura_chess/adapters/alice/models.py`, `src/yura_chess/adapters/alice/webhook.py`, `src/yura_chess/application/player_identity.py`, `tests/adapters/test_alice_webhook.py`; Modify `src/yura_chess/main.py`, `src/yura_chess/settings.py`

- [x] Валидировать обязательные поля протокола Алисы без логирования персональных payload целиком
- [x] Получать `skill_id`, `session_id`, `message_id`, строить fingerprint значимых полей и использовать их только как составной replay key
- [x] Псевдонимизировать `user_id`, а при его отсутствии ограниченно использовать `application_id`, с секретным server-side salt
- [x] Проверять владельца для любого `game_id` из Alice state; чужой или повреждённый state не должен раскрывать существование и состояние партии
- [x] Возвращать минимальный `user_state_update` с `game_id` и ревизией
- [x] При точном replay возвращать сохранённый ответ или безопасно возобновлять pending turn; конфликт fingerprint отклонять без изменения партии
- [x] Определять поддержку экрана через `meta.interfaces.screen`
- [x] Ограничить размер `text`, `tts` и state до лимитов платформы и обеспечивать полный webhook deadline 4,5 секунды
- [x] Добавить golden tests для последовательности отдельных Alice requests, cross-user доступа, повторов и параллельных запросов одной партии
- [x] Mark completed

### Task 6: Реализовать CommandRouter, голосовое распознавание ходов и безопасный ASR-корпус

**Files:** Create `src/yura_chess/application/command_router.py`, `src/yura_chess/voice/normalizer.py`, `src/yura_chess/voice/move_resolver.py`, `src/yura_chess/voice/types.py`, `src/yura_chess/storage/transcript_repository.py`, `migrations/versions/0002_asr_transcripts.py`, `tests/voice/test_move_resolver.py`, `tests/storage/test_transcript_repository.py`; Modify `src/yura_chess/storage/models.py`, `src/yura_chess/settings.py`

- [x] Отделить команды управления, запросы позиции и шахматные ходы до изменения партии
- [x] Нормализовать русские названия фигур, вертикали, числа, служебные слова, взятия, рокировку и превращение
- [x] Для текущей позиции генерировать допустимые голосовые формы каждого легального хода
- [x] Возвращать `resolved`, `ambiguous` или `unmatched` с измеримой уверенностью и никогда не выбирать первый ход из нескольких совпадений
- [x] Добавить команду «что ты услышала» и состояние ожидаемого уточнения
- [x] Сохранять для улучшения корпуса только нормализованный текст, результат распознавания, псевдоним пользователя и минимальные технические метаданные
- [x] Не сохранять аудио, access token, исходный webhook-payload и прямые Alice identifiers; добавить настраиваемый retention и удаление просроченных записей
- [x] Построить параметризованные тесты на синтетических и реальных ASR-транскриптах и тесты privacy/retention ограничений
- [x] Mark completed

### Task 7: Добавить объяснение невозможных ходов

**Files:** Create `src/yura_chess/voice/illegal_move.py`, `tests/voice/test_illegal_move.py`; Modify `src/yura_chess/application/command_router.py`

- [x] Сохранять распознанные piece/source/destination даже при отсутствии легального совпадения
- [x] Объяснять пустое исходное поле, чужой цвет и занятое своей фигурой поле назначения
- [x] Объяснять геометрию фигуры и первую блокирующую клетку для ладьи, слона и ферзя
- [x] Объяснять оставление короля под шахом и ход, не устраняющий текущий шах
- [x] Объяснять правила пешки, превращения, взятия на проходе и рокировки
- [x] Использовать общий fallback только для недостаточно определённого намерения
- [x] Mark completed

### Task 8: Реализовать доступное описание позиции и TTS

**Files:** Create `src/yura_chess/presentation/position_speech.py`, `src/yura_chess/presentation/move_speech.py`, `src/yura_chess/presentation/response_composer.py`, `tests/presentation/test_speech.py`

- [x] Позволить спросить содержимое поля, расположение типа фигур, фигуры одной стороны и всю позицию
- [x] Выдавать полную позицию стабильными группами с продолжением командой «дальше»
- [x] Формировать отдельный `tts` только когда произношение отличается от display text; само озвучивание оставить платформе Алисы
- [x] Однозначно озвучивать вертикали, горизонтали, взятия, шах, мат, рокировку и превращение
- [x] Добавить медленный повтор координат без изменения состояния партии
- [x] Проверить каждый ответ без обязательной экранной информации
- [x] Mark completed

### Task 9: Добавить экранную доску и Yandex image cache

**Files:** Create `src/yura_chess/presentation/board_image.py`, `src/yura_chess/adapters/yandex_images.py`, `migrations/versions/0003_board_image_cache.py`, `tests/presentation/test_board_image.py`; Modify `src/yura_chess/presentation/response_composer.py`, `src/yura_chess/storage/models.py`

- [x] Рендерить PNG только в памяти по текущему board, цвету пользователя и последнему ходу
- [x] Вычислять стабильный position hash без записи PNG на постоянный диск
- [x] Загружать изображение в Yandex resources и через SQLAlchemy-модель сохранять только `position_hash -> image_id` и служебные timestamps
- [x] Ограничить загрузку изображения оставшимся бюджетом webhook и без карточки завершать голосовой ответ до общего deadline 4,5 секунды
- [x] Возвращать `BigImage` только для screen-capable request
- [x] Реализовать ограниченную очистку image IDs по TTL/LRU после проверки квот API
- [x] Проверить, что недоступность image API и очистка cache не влияют на voice/text response и не заполняют Firebat PNG-файлами
- [x] Mark completed

### Task 10: Подготовить Firebat deployment, MariaDB operations и релизный контур

**Files:** Create `Dockerfile`, `deploy/compose.staging.yml`, `deploy/compose.production.yml`, `deploy/deploy.sh`, `deploy/rollback.sh`, `deploy/nginx/chess.waxim.ru.conf`, `deploy/mariadb/backup.sh`, `deploy/mariadb/restore-smoke.sh`, `deploy/README.md`, `deploy/INFRASTRUCTURE.md`, `.github/workflows/ci.yml`, `tests/golden/test_full_games.py`; Modify `docker-compose.yml`, `.env.example`

- [x] Собрать non-root application image со Stockfish и read-only application filesystem; не включать MariaDB или секреты в image
- [x] Для Firebat staging подключить приложение к существующему `staging-mariadb` через отдельные database/user и private network без публикации DB-порта
- [x] Для production описать выделенный Incus-стек `yura-chess` с отдельной MariaDB 11.4, persistent DB volume и внутренней сетью
- [x] Выполнять `alembic upgrade head` отдельным release step до переключения приложения; запретить старт при несовместимой схеме
- [x] Добавить health checks приложения, MariaDB и worker pool, resource limits, restart policy и ротацию логов
- [x] Публиковать приложение только на container loopback и описать Incus proxy-device до host loopback
- [x] Настроить публичный `chess.waxim.ru` без basic-auth, с TLS, body/time limits и rate limiting
- [x] Реализовать идемпотентный deploy с immutable image tag, preflight migrations, health smoke и документированным rollback на предыдущий tag
- [x] Добавить регулярный `mariadb-dump`, копию в настроенное внешнее S3-совместимое хранилище, retention, контроль свободного места, alert при сбое и restore-smoke в отдельную временную базу; задокументировать восстановление перед cutover
- [x] Описать в `deploy/INFRASTRUCTURE.md` топологию test/production, источники конфигурации, секреты, порты, deploy/rollback, backup/restore и диагностические команды
- [x] Добавить CI lint/type/test, MariaDB integration service, migration check, условную Compose validation и запрет секретов в репозитории
- [x] Провести двадцать golden games, тест параллельных пользователей и насыщения pool (`tests/golden/test_full_games.py`); voice-only QA и screen-device QA до публичной модерации (skipped — ручная проверка на устройстве Алисы, чек-лист в `deploy/README.md`)
- [x] Mark completed

## Verification notes

Каждая задача выполняется отдельным логическим изменением. Сначала запускаются focused tests изменённого слоя, затем все применимые команды из `Validation Commands`. `docker compose config` выполняется только после появления Compose-файла. Реальный Stockfish используется только в smoke/integration проверке; unit suite использует fake workers. Repository integration tests и CI используют MariaDB 11.4, а не альтернативную тестовую БД.

Alice webhook проверяется на повторную доставку, совпавший replay key с другим fingerprint, отсутствие авторизованного `user_id`, чужой или повреждённый state, слишком длинную реплику, параллельные запросы одной партии, независимые запросы разных пользователей, насыщение пула и timeout Stockfish. Позиция до и после любой ошибки должна оставаться доказуемо согласованной с UCI-историей и состоянием `pending_engine_turn`; каждый ответ укладывается в 4,5 секунды.

Перед Firebat cutover выполняются миграции на копии production backup, локальный health smoke, `mariadb-dump`, restore-smoke во временную базу, проверка off-host копии и внешний HTTPS-запрос через nginx. Staging получает отдельные database/user в существующем `staging-mariadb`; production использует свою MariaDB в выделенном Incus-стеке. Production секреты и реальные `.env` не попадают в GitHub.

ASR-хранилище проверяется на отсутствие аудио, access token, полных payload и прямых Alice identifiers, а также на автоматическое удаление данных старше настроенного retention. Board renderer не пишет PNG на постоянный диск; в MariaDB остаются только ограниченные метаданные Yandex image cache.

## Risks / open questions

Главный первый риск — повтор webhook после timeout, когда пользовательский ход уже записан, а ответ Stockfish ещё вычисляется. Две короткие транзакции, составной replay key, fingerprint, `pending_engine_turn` и сохранённый response реализуются до реального теста Алисы.

При публичной нагрузке два Stockfish worker могут быть заняты одновременно. Bounded pool не создаёт бесконечную очередь: acquisition timeout и трёхсекундный search deadline оставляют время на контролируемый Alice response в пределах 4,5 секунды, а pending turn безопасно возобновляется следующим запросом.

Ошибки ASR происходят до webhook и не могут быть полностью устранены кодом навыка. Компенсация строится на контексте легальных ходов, privacy-safe корпусе нормализованных транскриптов и безопасном переспросе без изменения доски.

Yandex image API имеет квоты и требует проверки жизненного цикла загруженных изображений. Экранная карточка не блокирует голосовой MVP; при недоступности image API навык возвращает полноценный voice/text ответ и не сохраняет PNG на Firebat.

Firebat является домашним failure domain. Отдельная production MariaDB, регулярный dump с off-host копией и alert, проверяемый restore, health monitoring и понятный fallback при недоступности движка обязательны до публичного релиза. Блокирующих открытых продуктовых или архитектурных вопросов нет.
