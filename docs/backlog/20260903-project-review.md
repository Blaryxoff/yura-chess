---
worth: yes
added: 2026-09-03
---
# Whole-project review — 51 findings, 9 blocking

Read-only review of the whole repository at HEAD `672e049` (working tree clean), run on 2026-09-03 by seven
parallel reviewers with separate lenses: security/data-correctness, architecture/performance, business-logic
completeness, ops/deploy/CI, test suite, documentation, and the HTML/CSS emitted from `presentation/`.

**Codex cross-check: skipped — the operator explicitly directed a Claude-only review.**

Nothing here was changed. Every Blocking finding below was re-verified by hand against the code before filing;
all voice-routing claims were re-measured through `route()` at HEAD rather than read from `asr_transcripts.outcome`.

**Outcome: FIX REQUIRED.** Significant gate: fail — promoted the four voice/flow defects
(trainer toggle, help exit, no-game catch-all, contradicted piece name) to Blocking because each one produces a
wrong action from a correctly-heard phrase on the voice-only path, which is the product's primary surface.

Scope note: `.devkit/toolkit.json` enables `devkit-frontend`, but this repo has no frontend stack — no
`package.json`, no JS/CSS/Vue files. That reviewer was scoped to emitted markup only. No Laravel coverage applies.

---

## Blocking

### 1. [SECURITY] The Alice webhook authenticates nothing

`/webhooks/alice` and `/alice/webhook` accept a bare `AliceRequest` with no dependency, header check or shared
secret. `settings.yandex_skill_id` exists but is read in exactly one place — the image client — and
`payload.session.skill_id` is copied straight into the replay key without ever being compared to it. nginx proxies
both paths with no `allow`/`deny`.

Any internet caller can therefore drive the whole state machine with a self-chosen `user_id`: create and resign
games, saturate the two-worker Stockfish pool so real players get «ещё думаю», push arbitrary positions through
`_upload_if_allowed` to burn the Yandex image quota, and inflate the public dashboard — the same usage data the
project grounds its marketing on.

- Evidence: `src/yura_chess/adapters/alice/webhook.py:102-104`, `:202`; `src/yura_chess/settings.py:49`;
  `src/yura_chess/adapters/yandex_images.py:119`; `deploy/nginx/yurachess.ru.conf:131`, `:155`
- Verified: no auth dependency on either route; `yandex_skill_id` has exactly one consumer.
- Fix: when `settings.yandex_skill_id` is set, reject any request whose `payload.session.skill_id` differs — before
  `owner_key` is derived and before any replay key is claimed. Return the neutral `_plain` answer rather than a 403,
  so a misconfiguration does not read to Yandex as a broken skill.

### 2. [SECURITY] `publish.yml` will build and push fork-PR code as a deployable image tag

`workflow_run` fires for every completed run of `CI`, and CI also runs on `pull_request`. The `branches: ["master"]`
filter matches `workflow_run.head_branch`, which for a fork PR is the *fork's* branch name. A PR opened from a fork
branch named `master` satisfies the filter; the publish job then runs in the base-repo context with
`packages: write`, checks out `github.event.workflow_run.head_sha` (attacker-controlled) and pushes it to
`ghcr.io/blaryxoff/yura-chess:<sha>`. `deploy.sh` accepts any 7–40 hex tag, so the poisoned tag is
indistinguishable from a legitimate one at deploy time.

- Evidence: `.github/workflows/publish.yml:4-7`, `:21`, `:26-27`; `.github/workflows/ci.yml:6`; `deploy/deploy.sh:20`
- Verified: config read directly; the filter and the `pull_request` trigger are both exactly as described.
- Fix: add `github.event.workflow_run.head_repository.full_name == github.repository &&
  github.event.workflow_run.event == 'push'` to the job `if`.

### 3. [SECURITY] `proxy_protocol` listeners have no `real_ip` config — the rate limiter is one global bucket

Both public server blocks listen with `proxy_protocol`, but nothing sets `set_real_ip_from` / `real_ip_header
proxy_protocol`. `$remote_addr` therefore stays the stream proxy's `127.0.0.1`, so `limit_req_zone
$binary_remote_addr` keys every request in the world into one bucket: a single client sustaining >200 r/s on
`/webhooks/alice` pushes all real Alice traffic to 429. The `X-Real-IP`/`X-Forwarded-For` handed to the app are
always `127.0.0.1`, and access logs carry no client address. The config's own comment claims the opposite.

- Evidence: `deploy/nginx/yurachess.ru.conf:16`, `:41`, `:54`, `:132`; `deploy/nginx/chess.waxim.ru.conf:12-14`
- Verified: `grep` over `deploy/nginx/` returns no `real_ip` directive anywhere.
- Fix: add `set_real_ip_from 127.0.0.1; set_real_ip_from ::1; real_ip_header proxy_protocol;` at http level in both
  vhosts, or key the zone on `$proxy_protocol_addr`.

### 4. [PERFORMANCE] Every request-path database call blocks the event loop, so the deadline guards cannot fire

`create_database_engine` builds a *synchronous* SQLAlchemy engine and `session_scope` is a plain context manager,
yet every application service calls it directly from `async def` coroutines. `run_in_threadpool` appears 11 times in
`src/` — all in `main.py`, `yandex_images.py` and `stockfish.py`, and **zero** times in `application/`, which holds
44 `session_scope` call sites.

This is not a style note. The two deadlines the architecture depends on are event-loop timers:
`asyncio.timeout(webhook_deadline_seconds)` in the webhook and `asyncio.wait_for(..., engine_acquire_timeout_seconds)`
in the pool. A blocking socket read on the loop defers timer callbacks, so under concurrency request B's 4.5 s budget
and 0.5 s acquire timeout cannot fire while request A is blocked in pymysql. The invariant "a search runs off the
event loop under a hard deadline" is enforced for Stockfish and silently unenforceable for everything around it. It
also serialises all traffic behind one thread while `database_pool_size=5` sits idle.

- Evidence: `src/yura_chess/storage/database.py:29`, `:43-54`; `src/yura_chess/application/conversation.py:230`,
  `:292-294`; `src/yura_chess/application/game_service.py:248`, `:325`, `:336`;
  `src/yura_chess/adapters/alice/webhook.py:113-114`; `src/yura_chess/engine/stockfish.py:305`;
  contrast `src/yura_chess/main.py:95`, `src/yura_chess/adapters/yandex_images.py:225`
- Verified: sync `Engine`, sync `session_scope`, `async def handle` calling it at `conversation.py:230`; 44 vs 0 counted.
- Fix: wrap each transactional body in `run_in_threadpool` (the pattern `BoardImageService.image_id_for` already
  uses), or move storage to an async driver. Cheap guard: no `session_scope` call may appear lexically inside an
  `async def`.

### 5. [INCONSISTENT] «Выключи режим тренера» turns the trainer ON

The `ENABLE` pattern is tried first and contains a bare, unanchored `режим тренера` alternative, so it swallows the
negated form. In an honest game this silently switches coaching on — the plan requires the trainer to be enabled only
by an explicit request with consent. In a training game the user asking to stop is told «Режим тренера уже включен.»
and has no way to switch it off with that phrase.

Measured through `route()` at HEAD:

| utterance | routed to |
|---|---|
| `выключи режим тренера` | `TrainingQuestion.ENABLE` |
| `отключи режим тренера` | `TrainingQuestion.ENABLE` |
| `выключи тренера` | `TrainingQuestion.DISABLE` (correct) |

- Evidence: `src/yura_chess/application/command_router.py:991-1005` (ENABLE at `:993-997` precedes DISABLE at
  `:1000-1005`); `src/yura_chess/application/training_service.py:94-97`, `:196-200`
- Fix: anchor the ENABLE alternative to an enabling verb (`(включи|запусти|давай)\w*\s+режим тренера` / `^режим
  тренера$`), or test `_TRAINING_PATTERNS` for DISABLE before ENABLE.

### 6. [MISSING] «Выйти из помощи» offers to close the skill instead of the help section

`HELP_EXIT` matches only the stem «справк». Measured through `route()`: `выйти из помощи` → `exit_confirm`,
`выход из помощи` → `exit_confirm`, while `выйти из справки` → `help_exit`. `EXIT_CONFIRM` is handled before any help
navigation, so the player who wanted to leave the help is asked «Выйти из навыка?» — answering «да» ends the Alice
session. `help_speech.navigate()` returns `None` for every exit wording, so nothing downstream recovers it. The
product plan names «выйти из помощи» verbatim as supported help navigation.

- Evidence: `src/yura_chess/application/command_router.py:307`; `src/yura_chess/application/conversation.py:430-435`;
  `docs/plans/product/20260719-human-like-chess-experience.md:44`
- Fix: add `помощ` to the HELP_EXIT stem alternation, and let `help_speech.navigate` claim exit wordings while
  `state.help` is open.

### 7. [MISSING] The no-game catch-all starts a game for any unhandled utterance — and can resign the running one

When `game is None`, only `GAME_FACT`, `LEVEL_QUERY` and `CONTINUE` are answered; everything else falls through to
`_start()` at `conversation.py:859`. Measured with no board: `сдаюсь`→`resign`, `отмени ход`→`undo`,
`ничья`→`claim_draw`, `хочу пиццу`→`unknown` — all of these create a new game with no confirmation, contradicting the
UX principle that a new game is destructive and needs confirming.

Worse: `_start` → `start_game` → `resign_active_games`, and that call is unconditional on the created path: it marks *every* `ACTIVE` game for the owner `RESIGNED`. If the client state ever arrives without a `game_id` — the
exact hazard the colour-choice branch guards against at `conversation.py:689-690` with the comment "The session may
have lost the game the player is in" — one unrecognised word marks the player's running game `RESIGNED`. Only the
`CONTINUE` branch consults `find_latest_active_game` first.

- Evidence: `src/yura_chess/application/conversation.py:840-859`; `src/yura_chess/application/game_service.py:143`;
  `src/yura_chess/storage/game_repository.py:122-135`
- Fix: apply the same `find_latest_active_game` fallback before `_start` at `:859`; answer
  `UNKNOWN`/`RESIGN`/`UNDO`/`CLAIM_DRAW`/`POSITION_QUERY` with «Партии сейчас нет…» and start a game only from
  `START`/`NEW_GAME`/an empty new-session launch.

### 8. [INCONSISTENT] A contradicted piece name is discarded and the move applied at full confidence

`_focused_signatures` marks a slice move-shaped on `squares >= 2` alone, ignoring any `PIECE` token; `resolve` then
re-runs `recognize` on the matched slice, dropping the piece the speaker named, and reports the full
`_FULL_COORDINATES` tier. Measured on the start position through `route(..., board=chess.Board())`:

| utterance | routed to |
|---|---|
| `конь е два е четыре` | `move e2e4` |
| `ладья е два е четыре` | `move e2e4` |
| `ферзь е два е четыре` | `move e2e4` |
| `слон г одна эф три` | `move g1f3` |

A mis-heard or mis-said piece name silently moves a different piece, against the MVP criterion that the position is
not changed on an ambiguous phrase, and against the requirement to explain the impossible move instead.

- Evidence: `src/yura_chess/voice/move_resolver.py:156`, `:79`, `:84-89`; gate at
  `src/yura_chess/application/command_router.py:1649`
- Fix: when the slice carries a `PIECE` token that disagrees with `board.piece_at(source)`, drop the tier below the
  confidence threshold so the turn becomes a clarification, or route it to the illegal-move explainer with a
  "wrong piece named" reason.

### 9. [DEFECT] The one test named for invariant 6 cannot fail, and nothing else covers it

`test_speech_is_identical_with_and_without_a_screen` asserts
`compose_turn(result).text == compose_turn(replace(result)).text`. `replace()` with no keyword arguments returns an
identical dataclass copy, and `compose_turn` takes no screen argument at all. The assertion is `f(x) == f(x)` on a
pure function — structurally incapable of failing, while carrying the invariant's name.

The invariant's real half — a card may not carry required information the speech lacks — has **no test anywhere**.
What exists covers only the reverse direction. `test_board_image.py:340` asserts `BigImageCard(title="Ваш ход")`
while the stub speech is «Мой ход. Е2 Е4.» — card title and speech asserted independently, never against each other,
and the title is whose turn it is.

- Evidence: `tests/presentation/test_board_image.py:180-183`; `src/yura_chess/presentation/response_composer.py:56-61`;
  `tests/adapters/test_alice_webhook.py:1049-1056`; `tests/e2e/test_alice_sessions.py:79-81`
- Verified: `compose_turn` signature confirmed to take no screen parameter.
- Fix: one parametrized test over the card-producing replies (board card, help `ItemsList`, PGN `TextCard`, puzzle
  card) asserting card text ⊆ speech text, with the review button explicitly exempted.

---

## Significant

### Voice and product flows

**[IDEMPOTENCY RISK] Review dictation turns its page with a relative write and no replay claim.** `dictate()` computes
`review.page + step`, and `ReviewService` never receives a `RequestContext` — it makes no `record_request` claim
anywhere, unlike `PuzzleService.hint` and `TrainingService._advance_hint` (whose docstring states the claim is what
makes an absolute write idempotent). The only guard is the webhook's whole-response cache, written *after* the answer
and skipped on the deadline timeout and on a cache-write failure. A redelivered «дальше» advances the dictation
twice. The module docstring claims the opposite.
Evidence: `src/yura_chess/application/review_service.py:94-97`, `:11`; `src/yura_chess/application/conversation.py:645-651`;
`src/yura_chess/adapters/alice/webhook.py:126-148`.
Fix: pass `RequestContext` into `dictate` and claim the replay key before moving the cursor, or send an absolute page.

**[IDEMPOTENCY RISK] `start_branch` creates a durable game row with no replay claim.** The confirmed «сыграть эту
позицию заново» path calls `GameRepository.create_game` unconditionally. `start_branch` first runs up to
`PLIES_PER_REQUEST` engine analyses, making the webhook deadline the most likely failure mode on exactly this path;
on retry the pending action is still in session state, the confirmation is re-applied, and a second branch game is
created — which also resigns the first via `resign_active_games`.
Evidence: `src/yura_chess/application/review_service.py:124-147`; `src/yura_chess/application/conversation.py:582-591`.

**[INCONSISTENT] «Выключи подсказки» / «не надо подсказок» request a hint.** The `HINT` pattern matches the stem
`подсказ` with no negation guard and no anchor. Measured: both route to `TrainingQuestion.HINT`. In an honest game
this answers the refusal with the trainer offer; in training mode it delivers a hint **and advances the stored hint
stage** — a state mutation from a phrase asking for the opposite.
Evidence: `src/yura_chess/application/command_router.py:1047-1053`; `src/yura_chess/application/training_service.py:300-335`.

**[MISSING] Advertised puzzle request «почему решение правильное» has no code path.** The product plan lists it among
supported puzzle phrases. `PuzzleQuestion` has no matching member and no regex covers it — `SOLUTION` requires
`покажи|скажи|какое|объясни|не знаю` before «решение». Measured: `route('почему решение правильное')` → `unknown`.
Evidence: `src/yura_chess/application/command_router.py:178-187`, `:1098-1157`;
`docs/plans/product/20260719-human-like-chess-experience.md:107`.

**[INCONSISTENT] Rules-help phrasings without «шахмат» reach help and then miss the rules section.** The router
deliberately accepts rules wordings without «шахматы»; `answer_help` re-runs `is_rules_request`, which hard-requires
the stem «шахмат». Measured: `научи меня играть`, `научи играть`, `я не умею играть` → HELP, then «Такого раздела в
справке нет.»; `научи игре` → the **«партия»** section.
Evidence: `src/yura_chess/presentation/help_speech.py:339`, `:347`, `:357-370`;
`src/yura_chess/application/command_router.py:325-338`.

**[INCONSISTENT] «Где я ошибся?» claims there were no mistakes when the engine never valued anything.**
`last_mistake` returns `None` both when there are no mistakes and when no checkpoint exists. Checkpoints come only
from `observe_player_move`, which analyses via `analyse_background` → `_acquire(wait=False)` →
`EngineUnavailableError` whenever no worker is immediately idle — and the failure is swallowed. Under load the
trainer makes a positive false factual claim instead of the controlled "engine busy" answer.
Evidence: `src/yura_chess/application/training_service.py:337-340`, `:156-167`; `src/yura_chess/engine/stockfish.py:248`,
`:297-298`; contrast `src/yura_chess/application/review_service.py:397-400`.

**[MISSING] A puzzle is presented without its goal or its difficulty.** `present()` produces only «Задача, ход {side}.
{reading} Назовите лучший ход.» The plan requires announcing the side to move, the goal, the difficulty, and to
*offer* to read the position. `puzzle.themes`, `puzzle.rating` and `puzzle.bucket` are all available and none is used;
the position is read unconditionally instead of offered.
Evidence: `src/yura_chess/application/puzzle_service.py:247-253`;
`docs/plans/product/20260719-human-like-chess-experience.md:110`.

**[INCONSISTENT] «Все команды» reads capabilities the current mode cannot use, with no caveat.** `_UNAVAILABLE_NOTES`
has no `HelpTopic.ALL` key, and `_render` looks the note up by requested topic and attaches it only to page 0. In
`NO_GAME`, `GAME_OVER` and `PUZZLE` it reads the trainer, review, moves and position sections with no availability
note anywhere.
Evidence: `src/yura_chess/presentation/help_speech.py:254-283`, `:459`.

**[INCONSISTENT] A themed puzzle request can be falsely refused.** The candidate pool excludes the currently open
puzzle *before* the theme filter is applied, and the theme filter has no `or pool` fallback (unlike the bucket filter
below it). If the only catalogue entry carrying the requested theme is the one already open, Alice answers «Задач на
эту тему у меня нет.» — untrue.
Evidence: `src/yura_chess/application/puzzle_service.py:267-274` vs `:283`.

### Security and data correctness

**[MISSING] A timed-out image upload orphans a remote image nothing can reclaim.** `YandexImageClient.upload`
swallows `TimeoutError` and returns `None`, so an upload the Dialogs API accepted but answered slowly (1.0 s default)
leaves an image stored remotely while `_store` is never reached and no `board_image_cache` row is written.
`maintain_cache` only considers `eviction_candidates` read from that local table, so the orphan is invisible to
eviction forever and permanently counts against the quota `_quota_exhausted` gates on. Under a slow API the failure
is self-reinforcing: each timeout both loses the card and consumes quota.
Evidence: `src/yura_chess/adapters/yandex_images.py:142-147`, `:279-283`, `:239-252`, `:315-317`.

**[SECURITY] The raw Alice `session_id` is written to logs, defeating the project's own pseudonymisation.** Both error
paths log it verbatim. That identifier is exactly what the analytics layer refuses to store: `usage_requests.session_key`
is `sha256(skill_id \0 session_id)` and `asr_transcripts.request_key` is `sha256(skill_id \0 session_id \0 message_id)`.
Anyone with log access can recompute both keys and pull that dialogue's usage rows and retained ASR transcripts out of
the "privacy-safe" tables.
Evidence: `src/yura_chess/adapters/alice/webhook.py:144`, `:147`; `src/yura_chess/storage/usage_repository.py:312-318`;
`src/yura_chess/storage/models.py:114`, `:338`; `src/yura_chess/application/player_identity.py:1-6`.
Fix: log `usage_request_key(...)` or the derived `owner_key` instead — equally useful for correlation, no platform identifier.

**[SECURITY] `identity_salt` has no guard against the placeholder published in `.env.example`.** The setting correctly
has no default, so a missing value fails startup. But `.env.example` ships a concrete value, `SecretStr` imposes no
constraint, and nothing rejects it when `environment == "production"`. A stack configured from a copied example
pseudonymises every owner key with a salt that is public in the repository. `INFRASTRUCTURE.md:85-86` names this as
the one secret whose compromise is unrecoverable.
Evidence: `src/yura_chess/settings.py:23-24`; `.env.example:5-6`; `deploy/INFRASTRUCTURE.md:85-86`.

### Architecture

**[PERFORMANCE] The same replay row is read in up to five separate transactions before a turn does any work.**
Idempotency is implemented four times over, each with its own `session_scope` issuing the identical
`get_request_replay(...)` query, fanned out sequentially at the top of every request: `cached_alice_response`, then
`replayed_response`, then `replayed_level_change`, then `resume_request`. On the new-session path `request_was_seen`
adds a fifth. Combined with Blocking #4, that is five blocking round-trips on the loop before routing begins.
Evidence: `src/yura_chess/application/game_service.py:206-211`, `:217-226`, `:282-292`, `:294-303`;
`src/yura_chess/application/puzzle_service.py:109-117`; `src/yura_chess/application/conversation.py:239`, `:255-256`.

**[FORBIDDEN] `application/command_router` imports its input-recognition patterns from `presentation/`.** The module
that decides what an utterance *means* sources its regexes and predicates from the presentation layer:
`game_facts.QUESTION_PATTERN`, `is_rules_request`, and four compiled patterns from `position_speech`. Recognition is a
`voice/` concern; the dependency runs `application → presentation → voice.normalize` instead of `application → voice`,
and three presentation modules each re-run `normalize()` on an utterance the router already normalised.
Evidence: `src/yura_chess/application/command_router.py:24-26`, used at `:771`, `:779-788`, `:1279`.
Verified by direct read; `domain/` by contrast has no upward imports at all.

**[FORBIDDEN] `GameService` and the storage schema know Alice's wire format.** The application service exposes
`cached_alice_response`, `store_alice_response` and `store_alice_response_with_review_prompt`, and the column is
literally named `alice_response_payload`. The last encodes an Alice-specific product concept (a screen-card review
button and its two serialisations) into the game service.
Evidence: `src/yura_chess/application/game_service.py:217`, `:228`, `:240`; `src/yura_chess/storage/models.py:306`.
Verified by direct read.

**[DUPLICATION] `ConversationService._handle` is a 617-line god method with 44 `routed.kind` branches.** Consequences
visible in the same body: `self._puzzles.find_open(owner_key)` (its own transaction) is called at `:352`, `:384`,
`:552`, `:570` and `:598` — two of those can fire on one path; `find_latest_active_game` at six sites. The identical
state-reset block is written out three times.
Evidence: `src/yura_chess/application/conversation.py:332-949`; resets at `:241-248`, `:261-269`, `:405-412`.

**[DUPLICATION] `route(utterance)` is executed four times per request, three of them for one field.** `handle` calls
it at `:243`, `:263` and `:275`, discarding everything but `.normalized.text` or `.kind`, then `_handle` routes again
at `:377` — this time with the board and pending clarification, so the earlier three see a *different* interpretation
than the one actually used.
Evidence: `src/yura_chess/application/conversation.py:243`, `:263`, `:275`, `:377`.

**[INCONSISTENT] Types sit on the wrong side of two layer boundaries.** (a) `presentation/dashboard.py` imports its
input DTOs `DashboardSnapshot` and `UsageTotals` from `storage/usage_repository.py`, while `ChartPeriod` lives in
storage and `ChartMetric` in presentation — two halves of one view model split across layers. (b)
`adapters/alice/models.py`, the Alice wire schema, imports two of its own platform constraints
(`CARD_DESCRIPTION_LIMIT`, `CARD_ITEMS_LIMIT`) from `presentation/response_composer.py`, while `TEXT_LIMIT`,
`TTS_LIMIT` and `CARD_TITLE_LIMIT` are defined locally.
Evidence: `src/yura_chess/presentation/dashboard.py:11`, `:13`; `src/yura_chess/storage/usage_repository.py:18`,
`:36-67`; `src/yura_chess/adapters/alice/models.py:15`, `:18-21`; `src/yura_chess/presentation/response_composer.py:30-31`.

### Ops, deploy and CI

**[INCONSISTENT] `workflow_dispatch` publishes a deployable tag with no CI gate.** The job's `if` short-circuits on
`github.event_name == 'workflow_dispatch'`, and that path tags with `github.sha` from whatever ref was chosen.
AGENTS.md and `deploy/README.md:164` both treat green CI as the gate, but a manual dispatch on any branch produces an
indistinguishable deployable tag with red or absent CI.
Evidence: `.github/workflows/publish.yml:21`, `:42`.

**[MISSING] `deploy.sh` rollback aborts on a failed pull, leaving the broken image serving and the state file lying.**
`rollback()` is invoked from a `then` block, so `set -e` applies inside it. If `compose pull --quiet app` for the
previous image fails, the script exits at line 63 with the *new, unhealthy* container still running — no rollback, no
message, and `production.current-image` still names the old tag, so operators and `rollback.sh` both believe the old
image is live. Line 64 already guards `compose up` with `|| true`; line 63 does not.
Evidence: `deploy/deploy.sh:59-68`, `:103-107`, `:110-114`.

**[STALE] `restore-smoke.sh` never verifies the Alembic revision is at head.** The script's header and three documents
state that it proves Alembic is at head. Lines 75-80 only assert `alembic_version` returns a non-empty string. A dump
from a schema many releases old — the exact case a restore drill is meant to catch — passes and is reported verified.
Evidence: `deploy/mariadb/restore-smoke.sh:6-7`, `:75-80`; `deploy/INFRASTRUCTURE.md:145-146`; `deploy/README.md:114-115`.

**[MISSING] A stopped backup timer reports success indefinitely.** `ConditionPathExists=/srv/yura-chess/backup.env`
makes the unit exit as condition-not-met, which systemd records as success. If `backup.env` is deleted or renamed,
both the daily backup and the weekly restore-smoke silently stop while `systemctl list-timers` and `is-failed` stay
clean. Neither unit has `OnFailure=`, and `YURA_CHESS_BACKUP_ALERT_COMMAND` is empty by default. Decoupling backup
status from deploys is by design; the separate monitoring that design relies on has no signal to read.
Evidence: `deploy/systemd/yura-chess-backup.service:5`; `deploy/systemd/yura-chess-restore-smoke.service:5`;
`deploy/mariadb/backup.sh:12-21`; `.env.example:67`.

**[MISSING] No CI gate on the deploy scripts at all.** `deploy/*.sh` is the entire release mechanism, is copied to
production by hand, and is never checked by CI — not `shellcheck`, not even `bash -n`. A syntax error or mis-quoted
expansion is discovered on Firebat during a release. The `# shellcheck disable=SC1090` pragmas show shellcheck was
intended.
Evidence: `.github/workflows/ci.yml:50-86`; `deploy/mariadb/backup.sh:19`; `deploy/mariadb/restore-smoke.sh:18`.

**[MISSING] Migrations run against the live previous release with no lock-free DDL requirement.** `deploy.sh` runs
`alembic upgrade head` to completion while the *old* app container still serves traffic. `deploy/README.md:59-61`
requires only that each migration stay schema-compatible with the previous release; nothing requires the DDL itself to
be non-blocking. `0004` runs `CONVERT TO CHARACTER SET` and `0013` runs `MODIFY updated_at DATETIME`, both table-copy
rebuilds of `games` under MariaDB. On a grown table these hold a write lock for the duration, and Alice abandons any
request over ~5 s — so a routine deploy becomes an outage with no rollback trigger, because the old app is still healthy.
Evidence: `deploy/deploy.sh:90-107`; `migrations/versions/0004_retention_indexes.py:46`;
`migrations/versions/0013_query_order_and_timestamps.py:46-50`; `deploy/README.md:59-61`.

### Emitted markup and the public site

Escaping was checked explicitly and is **clean**: every value interpolated into the emitted HTML is an `int`, a
`date`, or a `Literal`-constrained query parameter FastAPI rejects with 422 before it reaches a template. There is no
user- or DB-derived string in the markup, so there is no injection finding.

**[MISSING] Board coordinates are unreadable at the size Alice actually displays the card.** The coordinate ring is
drawn at `BORDER_PIXELS - 2` = 10 px inside a 488 px board. The project's own test fixes the Yandex BigImage display
ratio at `552 / 245`, so the 1120×500 PNG is shown at 552×245 — scale 0.49, putting file letters and rank digits at
~4.9 px. Rendered and rescaled during review: pieces stay recognisable, coordinates become smudges. The catalogue copy
promises «показываются доска с координатами».
Evidence: `src/yura_chess/presentation/board_image.py:125`, geometry at `:21-25`; `tests/presentation/test_board_image.py:92`.
Fix: `BORDER_PIXELS = 20`, `SQUARE_PIXELS = 56` — `BOARD_PIXELS` stays 488, so the existing
`CARD_HEIGHT - BOARD_PIXELS <= 12` assertion still passes and the coordinate font rises to ~9 px as displayed.

**[INCONSISTENT] The site nav and footer live inside `<main>`, so nothing lets a keyboard user bypass the repeated
links.** `_document` puts the 7-link nav and `FOOTER_HTML` inside `<main>`. A `<footer>` descended from `<main>` is
not exposed as the `contentinfo` landmark, and because the nav is also inside `<main>`, jumping to the main landmark
does not skip the repeated block either. There is no skip link. WCAG 2.4.1 has no satisfying mechanism on a site whose
stated primary audience is screen-reader users.
Evidence: `src/yura_chess/presentation/website.py:732-738`; `docs/seo/campaign.md` (audience 1).

**[INCONSISTENT] On phones the chart caps the tallest bars, flattening the top of the value range.** `.stats-chart` is
`height: 230px` at ≤520 px with `padding: 18px 52px 58px 12px`, giving a 154 px content box. Each `.stats-day` holds a
12 px label at `line-height: 1.6` (19.2 px), a 5 px gap, and a bar reaching 150 px at the peak — 174.2 px required.
The label's `min-height: auto` cannot shrink, so all 20.2 px of shrinkage falls on the bar: rendered height is
`min(h, 129.8)`, and every value between 86.5% and 100% of the peak draws identically. Desktop is unaffected, so the
chart lies only on the device most readers use.
Evidence: `src/yura_chess/presentation/dashboard.py:286`, `:141-157`, `:300`.

**[INCONSISTENT] The phone padding fix did not reach the tooltip anchored to the same card.** `fix(site): fit the
statistics cards on a phone` (9d4d724) tightened `.stats-card` to `padding: 11px`, but `.stats-tip` still insets
`left: 12px; right: 12px` from that card — an inset calibrated for the old 18 px padding. At 390 px the card is
~153 px wide, so ~180 characters of Russian at 14 px render in a ~129 px column, roughly 13 lines tall; at 320 px it
is ~94 px. The fix is a one-off for the numbers, not for everything anchored to the card.
Evidence: `src/yura_chess/presentation/dashboard.py:213-217` against `:282-283`.

**[PERFORMANCE] Every secondary page inlines the dashboard CSS and the whole statistics script it can never use.**
`SITE_CSS` is site rules + `DASHBOARD_CSS` (23,621 chars) and `SITE_SCRIPT` is 10,033 chars; both are inlined into
every document. `/commands` is 45,956 bytes, of which ~33.6 KB is CSS+JS containing 113 `stats-` selectors and a full
fetch/view-transition/IntersectionObserver controller matching nothing on that page — and being inline, none of it is
cacheable across the seven-page navigation the nav actively encourages.
Evidence: `src/yura_chess/presentation/website.py:392`, `:728`, `:739`.

### Documentation

**[STALE] The deploy runbook's migration range stops three revisions short.** It names `0007_player_preferences` …
`0015_reclassify_ping_monitors`; the repository also has `0016`, `0017` and `0018`, which the same one-shot `migrate`
container applies. An operator reading this to judge backwards compatibility is judging the wrong set.
Evidence: `deploy/README.md:63-66` vs `migrations/versions/0016_request_quality_analytics.py`, `0017_game_sounds.py`,
`0018_review_prompt.py`; `deploy/compose.production.yml:96`.

**[MISSING] THIRD_PARTY_NOTICES omits the DejaVu fonts the image redistributes.** The runtime image installs Debian
`fonts-dejavu-core` and the application loads those files to render board and social-card images, so every published
image redistributes the fonts under their own (Bitstream Vera derived) terms with no notice.
Evidence: `THIRD_PARTY_NOTICES.md:4-6`; `Dockerfile:27`; `src/yura_chess/presentation/board_image.py:35`;
`src/yura_chess/presentation/social_card.py:29-30`.

**[MISSING] `.env.example` does not list every variable, contrary to two documents that promise it does.** Absent:
`YURA_CHESS_DATABASE_POOL_SIZE`, `..._MAX_OVERFLOW`, `..._POOL_RECYCLE_SECONDS` and
`YURA_CHESS_VOICE_MOVE_CONFIDENCE_THRESHOLD` — the last being the threshold that decides whether a heard move is
played or questioned, a product invariant. Also missing: `YURA_CHESS_DEPLOYED_URL`, `..._HEALTH_URL`,
`..._HEALTH_ATTEMPTS`, `..._ENV_FILE`, `..._DB_ENV_FILE`.
Evidence: `README.md:169`; `deploy/INFRASTRUCTURE.md:77` vs `src/yura_chess/settings.py:27-29`, `:46`.

**[STALE] The puzzles roadmap describes shipped work as a future milestone.** It is titled "next product milestone"
and plans a `Puzzle` catalogue, a `PuzzleAttempt` entity, an explicit conversation mode and a "First puzzle release"
— all shipped, and already advertised to users. README still links it as forward-looking.
Evidence: `docs/product/puzzles-roadmap.md:1`, `:5` vs `migrations/versions/0011_puzzles.py`,
`src/yura_chess/application/puzzle_service.py`, `src/yura_chess/application/command_router.py:1171-1179`; `README.md:261`.

**[INCONSISTENT] The human-like-experience product plan is marked "ready to implement" while its dev plan is
complete.** The product plan carries «готов к реализации»; its dev plan has all 26 tasks and 170 checkboxes ticked
with `Status: completed 2026-07-30`, and the features are live. AGENTS.md tells every contributor to read the active
plans — with both complete, that instruction points at finished work. The same plan's validation commands reference
`deploy/compose.staging.yml` and `tests/e2e/test_staging_webhook.py`, neither of which exists, and
`deploy/README.md:69` states there is no staging environment.
Evidence: `docs/plans/product/20260719-human-like-chess-experience.md:3`;
`docs/plans/dev/20260719-human-like-chess-experience.md:351`; `AGENTS.md:4`.

**[INCONSISTENT] "The same checks as CI" lists neither the migration check nor the `secrets` job.** CI's `quality` job
also runs `alembic upgrade head && alembic check`, and a separate `secrets` job rejects committed `.env`/key/database
files and scans for credential-shaped strings. A branch passing all four documented commands can still fail CI.
Evidence: `CONTRIBUTING.md:27-34`; `README.md:203-211` vs `.github/workflows/ci.yml:62-67`, `:88-119`.

### Tests

**[MISSING] The confidence threshold is pinned by no test; 0.7 → 0.0 leaves the suite green.**
`voice_move_confidence_threshold` appears in exactly three places repo-wide — its definition and two call sites — and
in **zero** test files (verified: `grep -rn` over `tests/` returns 0). Every conversation-level CLARIFY test reaches
the AMBIGUOUS or UNMATCHED branch, neither of which consults the threshold. Lowering the shipped default to its floor
makes the skill guess on every misheard word with nothing failing. No test drives confidence below 0.7 the way
production does — via the unknown-word penalty; the only sub-threshold test passes a synthetic `confidence_threshold=0.9`.
Evidence: `src/yura_chess/settings.py:46`; `src/yura_chess/application/command_router.py:1649`;
`tests/voice/test_move_resolver.py:628-632`.

**[MISSING] The clarification speech is unasserted; `{choices}` could vanish silently.** `conversation.py:1442-1443`
builds «Ход неоднозначен. Уточните: {choices}.» The only test touching it asserts `"неоднозначен" in ...speech.text`
— dropping `{choices}` entirely keeps it green, so the skill would ask a question naming nothing to choose between.
The single-candidate confirmation and the promotion question are untested repo-wide (`grep -rn "Подтвердите\|Превратить"
tests/` returns nothing). There is no conversation-level ambiguity test on a real game at all.
Evidence: `src/yura_chess/application/conversation.py:1433-1443`; `tests/application/test_puzzle_service.py:635-641`.

**[MISSING] Claiming a draw is never exercised through a spoken command.** The `CLAIM_DRAW` branch is reached by no
test at any layer. `GameService.claim_draw` is tested directly, but nothing joins the router to it, and nothing
asserts what the skill says when a draw is granted or refused.
Evidence: `src/yura_chess/application/conversation.py:931-933`; `tests/application/test_conversation.py:1727`.

**[MISSING] Undo never reaches the Alice transport.** No webhook or e2e request ever says «отмени ход» — zero
occurrences of `отмен` in `tests/e2e/` or `tests/adapters/`. Real assertions exist only at `ConversationService`
level. Undo is a required game action on a voice-only device.
Evidence: `tests/application/test_conversation.py:358-382`; `tests/e2e/test_alice_sessions.py:24-33`.
Fix: add «отмени ход» to `MUTATIONS` — the existing retry test at `:108-130` then covers it for free.

**[MISSING] "Immediate failure past one waiter per worker" is indistinguishable from the acquire timeout.**
`test_saturated_pool_fails_fast_without_queueing` uses `engine_pool_size=1` with one holder and one caller, so
`_acquire` evaluates `0 >= 1` → False and takes the *waiter* path, raising after the timeout. The three-caller test
wraps `pytest.raises` around a `gather`, which cannot tell immediate rejection from the 0.1 s timeout, and no test in
the suite measures elapsed time. Deleting the queue bound leaves both tests green.
Evidence: `src/yura_chess/engine/stockfish.py:299-301`; `tests/engine/test_stockfish.py:125-140`.

**[MISSING] The 3 s engine deadline and the 4.5 s webhook budget are pinned nowhere.** Every engine test overrides
`engine_move_deadline_seconds` to 0.2, and no test asserts the shipped defaults. Raising it to 5.0, or
`webhook_deadline_seconds` past the Alice budget, breaks the invariant with a green suite — the ceiling lives only in
`Field(..., le=3.0)`. The repo already has this pattern for other config, so the omission is inconsistent rather than stylistic.
Evidence: `src/yura_chess/settings.py:26`, `:36-37`; `tests/engine/test_stockfish.py:24-36`.

**[MISSING] Five of the eight replay-guarded entry points have no redelivery test.** `start_game`, `continue_game`,
`resign`, `claim_draw` and `undo_turn` are never called twice with the same `RequestContext`. All 30 `start_game` call
sites use a fresh message id, so the retried-start branch — reuse the stored response, or reload the game the first
delivery created — is unexecuted, including its `LookupError`. Nothing anywhere proves a redelivered «новая игра»
opens exactly one game. `undo_turn` is the one with real state damage on a double-apply: two truncations take back
four plies instead of two.
Evidence: `src/yura_chess/application/game_service.py:145-152`; `tests/adapters/test_alice_webhook.py:167-175`;
`tests/application/test_game_service.py:386-395`.

**[MISSING] A move named while the engine owes its opening is silently discarded, and the branch is untested.** In
`play_move`, the `_engine_to_move(state)` branch never reads the `move_uci` argument. When `pending is None` —
reachable when the player took Black and the engine still owes the opening move — `token`, `player_move` and
`observed` are all `None`, so the player's spoken move is dropped without a word and the answer names only the
engine's reply. Whether that is intended is a product decision; as coverage it is a clean gap.
Evidence: `src/yura_chess/application/game_service.py:346-353`; `tests/application/test_game_service.py:588-600`.

**[DEFECT] The parallel-dialogue test's board replay asserts nothing.** The test closes by replaying each stored move
list onto a fresh board, but `chess.Board.push` does not check legality, so the loop cannot raise on a corrupted
history. The stated intent — "must not lose a single player move" — is checked only by `moves[0] == "e2e4"`; a lost or
duplicated engine reply passes.
Evidence: `tests/e2e/test_alice_sessions.py:243-248`.
Fix: use `board.push_uci(move)` (which raises) and assert `len(moves) == 2`.

**[VAGUE] Puzzle-mode illegal-move explanation is asserted only by a negative.** The puzzle branch is exercised solely
by `assert "не решает" not in illegal.speech.text` — which passes for an empty reply, a generic "Я не понял ход", or
any wrong diagnosis. The game-mode branch is by contrast well covered.
Evidence: `tests/application/test_puzzle_service.py:626-641`; contrast `tests/application/test_conversation.py:891-908`.

**[VAGUE] Six of thirteen illegal-move reasons never have their squares asserted.** For `EMPTY_SOURCE`,
`OPPONENT_PIECE`, `LEAVES_KING_IN_CHECK`, `DOES_NOT_ADDRESS_CHECK`, `NO_CAPTURE` and `EN_PASSANT` no test asserts
`Explanation.source`/`destination`/`blocker` — a regression naming the right rule about the wrong square passes. The
substring «брать некого» appears in both the NO_CAPTURE and EN_PASSANT messages, and two tests assert opposite reasons
for that same phrase.
Evidence: `tests/voice/test_illegal_move.py:151-174`; `src/yura_chess/voice/illegal_move.py:119-134`, `:193-209`.

---

## Minor

**Voice and flows.** Rejections give no way forward — `UNDO_REJECTED` and `DRAW_NOT_CLAIMABLE` name neither the reason
nor the next command (`response_composer.py:37-38`; `conversation.py:1184-1189`). Re-entering the skill after a
finished game gives no welcome and no menu, only «Партия уже окончена» (`conversation.py:363-374`, `:862-864`). An
unknown command after the game ends still says «Скажите ход» (`conversation.py:944-947`). Error replies drop the
pending confirmation and open clarification, because `_plain` builds a response with no `session_state` at all
(`webhook.py:331-336`) — worst mid-clarification, where the candidate list the answer refers to is not carried back.
Switching colour before the first move records the discarded game as a `RESIGNED` zero-move game, which then becomes
the only finished game `_reviewable` can hand to «разбери партию» (`conversation.py:1202-1208`).

**Engine.** `analyse()` and `analyse_background()` do not subtract acquisition time from their deadline, unlike
`best_move` — at the settings bounds that permits acquire + analysis + grace = 3.25 s, outside the stated budget by
configuration alone (`stockfish.py:206-212` vs `:224-252`; `settings.py:35`, `:40`). The two methods are otherwise
near-identical, and both `analyse_background` and `set_skill_level` are reached by `getattr` string lookup because
neither is declared on the Protocols that define the seam, so a typo degrades silently
(`stockfish.py:109-111`, `:121-123`; `training_service.py:64-72`, `:371`). A worker damaged during shutdown leaks its
Stockfish child: `_damage` detaches the process then returns early when `_running` is False, and `stop()` clears that
flag before walking the workers (`stockfish.py:321-324`, `:176-188`). `identity_salt` is reused as the key for the
pending-action HMAC, with domain separation incidental rather than explicit (`player_identity.py:36`; `webhook.py:604`).

**Data.** The board-image cache stamps rows with bare `datetime.now()` while every other writer uses naive UTC, so
correctness rests on `TZ: UTC` being present in a compose file that reaches production only by manual copy
(`yandex_images.py:224`, `:243`, `:95-102`).

**Performance.** The webhook `LookupError` path re-runs the whole conversation pipeline inside the deadline
(`webhook.py:227-229`). The board font is loaded and parsed from disk on every render, and the coordinate font on top
of it (`board_image.py:55`, `:115-121`, `:125`). One global `asyncio.Lock` serialises rendering across all 27 landing
variants (`main.py:145`, `:165-169`). Switching period or metric refetches the entire 52 KB landing page while the
`X-Requested-With: statistics` hint the client sends is ignored (`website.py:451`; `main.py:147-155`).

**Markup.** `aria-label` on a plain `<div>` is dropped for want of a role (`website.py:843`). `Clean-param` cleans
`period` but not `metric` (`website.py:50`). The Yandex verification file answers GET only, unlike every other
crawler-facing route (`main.py:199`). Tooltip listeners are bound twice on first load (`website.py:520`, `:599`).
`aria-hidden` sits on a scroll container Chrome makes focusable (`dashboard.py:328`). The sitemap carries no
`<lastmod>` (`website.py:64-76`). Two presentation modules export `CARD_WIDTH`/`CARD_HEIGHT` with different meanings —
1120×500 for Alice, 1200×630 for OG — and only the PNG magic bytes are tested (`board_image.py:24-25` vs
`social_card.py:16-17`). Coordinates bypass the font fallback chain the pieces use (`board_image.py:125`).

**Ops.** `.env.example` omits four settings two documents claim it enumerates. The image is not reproducible from a
commit: floating base tags and an unversioned `stockfish` apt package, so two builds of the same sha can carry
different engine strength (`Dockerfile:3`, `:5`, `:23`, `:26-28`). No `.dockerignore`, so `.env`, `.git` and
`artifacts/` go into every build context. The migration retry loop retries deterministic failures ten times against a
schema it may already have half-changed (`deploy.sh:90-100`). `rollback.sh` never updates `PREVIOUS_FILE`, so after a
rollback the tag rolled out of is unrecoverable from state, and it skips the `compose config --quiet` validation
`deploy.sh` performs (`rollback.sh:57-66`). The first backup run fails under `ProtectSystem=strict` because
`ReadWritePaths` grants only the child directory (`backup.sh:50`; `yura-chess-backup.service:19-20`). The nginx
runbook never creates the `sites-enabled` symlink (`README.md:141-143`). Nothing prunes superseded images on Firebat
(`deploy.sh:82-83`).

**Docs.** AGENTS.md narrows the lint command to `ruff check src tests` where CI runs `ruff check .` (`AGENTS.md:49`).
The crawlable-surface verification loop skips `/og.png` and the Yandex Webmaster proof, both allowlisted in the vhost
(`deploy/README.md:146-153`). THIRD_PARTY_NOTICES names MPL-2.0 among runtime dependencies, but the only MPL package
is `certifi`, reachable only through the `dev` extra the image excludes (`THIRD_PARTY_NOTICES.md:51-54`). The README
HTTP-surface list omits the live legacy webhook path `POST /alice/webhook` (`README.md:113-114`).

**Tests.** `test_a_slow_engine_answers_inside_the_platform_deadline` measures no time.
`test_is_stable_for_the_same_inputs` asserts a pure function equals itself. `test_unknown_words_lower_confidence`
survives neutering `_UNKNOWN_WORD_PENALTY` to 0.001. Three mode answers are asserted by bare truthiness, and
`assert "3" in ...` matches any digit in a Russian sentence (`tests/e2e/test_modes.py:100-103`). A
conditional-expression assert at `test_conversation.py:2613` works today but invites an edit that asserts nothing. The
level-change / resume ordering at `conversation.py:255-256` is load-bearing — swapped, a retried level change raises
`KeyError` on `body["fen"]` — and no test names the constraint. `TurnResult.to_payload`/`from_payload` has no
round-trip test, and nothing asserts a stored payload's `fen` and `moves` are mutually consistent. A missing
`YURA_CHESS_TEST_DATABASE_URL` silently skips roughly 600 of ~880 tests with no warning — a local hazard, not a CI gap
(`tests/database_fixtures.py:44-47`).

---

## Product invariant status

| # | Invariant | Behaviour | Test coverage |
|---|---|---|---|
| 1 | Every required action works voice-only | HOLDS | PARTIAL — undo and claim-draw uncovered at the Alice layer |
| 2 | Never apply an ambiguous / low-confidence move | **VIOLATED** — contradicted piece name (Blocking #8) | PARTIAL — threshold pinned by nothing |
| 3 | Explain an illegal move specifically | HOLDS — all 13 reasons verified live | PARTIAL — squares unasserted for six reasons |
| 4 | UCI history canonical, FEN derived | HOLDS | COVERED — `golden/test_full_games.py:164-167`, `storage/test_game_repository.py:73`, `:232-238` |
| 5 | Repeated requests idempotent, no second move | HOLDS for the chess turn; **VIOLATED** for review dictation and `start_branch` | PARTIAL — 5 of 8 entry points have no redelivery test |
| 6 | Cards never carry info absent from speech | HOLDS | **UNCOVERED** — the only named test is vacuous (Blocking #9) |
| 7 | Stockfish never decides legality | HOLDS — python-chess decides; the engine's own reply is re-validated | COVERED both ways — `test_game_service.py:143-152`, `:214-228` |
| 8 | Engine deadline ≤3 s covering acquire+search | HOLDS for `best_move`; analyse paths do not subtract acquisition | PARTIAL — shipped values pinned nowhere; the one-waiter bound survives deletion |

Per-entry-point idempotence (invariant 5): `play_move`, `set_level`, `resume_request` COVERED;
`start_game`, `continue_game`, `resign`, `claim_draw`, `undo_turn` UNCOVERED.

---

## Checked and found correct

Worth recording so it is not re-investigated:

- **No HTML injection.** Every value interpolated into the emitted markup is an `int`, a `date`, or a
  `Literal`-constrained query parameter FastAPI rejects with 422 first.
- **`domain/` has no upward imports** — the layering violations are confined to `application → presentation` and
  `adapters → presentation`.
- **All six `yura-chess-shell` CLI flags and exit words** match `cli.py`.
- **Every voice command advertised in README and `docs/yandex-skill-description.md`** was checked against the router:
  all eight puzzle themes, the 0–20 level range, the preview analysis and two-rank board paging are real. The 3803
  opening figure matches `openings.meta.json`.
- **Python 3.12 / MariaDB 11.4 / Stockfish requirements** agree across `pyproject.toml`, `Dockerfile` and the compose files.
- **Shared test state is clean** — `truncate_tables` is autouse-after-each in all five relevant conftests, so replay
  keys and games do not leak between tests.
- **~150 `assert X is not None`** are type narrowing before a real assertion in every case sampled, not stand-ins.
- Settled non-issues confirmed still settled: `httpx2` is the TestClient backend; `test_database_is_mariadb_11_4`
  correctly rejects MySQL locally; `deploy/*.sh` reaching production by manual copy is intentional; backup status not
  blocking a deploy is by design.

---

## How to consume this file

This is one aggregate file, as requested. The devkit backlog conduct wants one file per defect
(`docs/backlog/<defect-slug>.md`) so each can be triaged and `git rm`'d with its fix; splitting the nine Blocking
findings out into their own items is the natural next step if this queue is going to be worked through
`/backlog`. Nothing here has been committed.
