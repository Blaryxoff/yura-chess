# Project Guidelines

`yura-chess` is the backend for the Yandex Alice skill **«Шахматы с Юрой»**.
Before implementation, read the active product and dev plans under `docs/plans/`.

## Product invariants

- Every required game action must work on a voice-only Alice device.
- Never apply an ambiguous or low-confidence move; ask for clarification.
- Explain an illegal move specifically whenever the intended move can be reconstructed.
- Full UCI move history is canonical; FEN is only a derived snapshot.
- Repeated Alice requests must be idempotent and never produce a second move.
- Screen cards enhance the response but may not contain required information unavailable in speech.
- Stockfish never decides whether the player's move is legal.

## Architecture

- Runtime code lives under `src/yura_chess/`.
- Alice protocol adapters, application services, chess domain, storage, engine, voice, and presentation stay separate.
- Keep a modular monolith and a bounded pool of persistent Stockfish processes (two by default), each with its own lock.
- A search runs off the event loop under a hard deadline of at most 3 s; pool exhaustion returns a controlled answer instead of queueing.
- Keep full game state server-side; Alice state contains only identifiers, revision, and replay metadata.
- Render board images in memory. Any byte cache must be bounded and disposable.
- Do not copy code or vocabulary verbatim from `axtrace/alisa_chess` until licensing is explicit.

## Delivery

- Implement one `### Task N` from the active Ralphex dev plan at a time.
- Tests run automatically after code changes: focused tests first, then the full configured suite.
- Add tests with every voice phrase, state transition, rule diagnostic, and regression.
- Prefer captured Alice ASR transcripts over invented synonyms.
- `asr_transcripts.outcome` records how the build deployed at the time routed the phrase, not how HEAD routes it. Read
  the corpus for what players say; re-measure routing through `route()` before concluding anything about today.
- Never commit `.env`, credentials, Yandex tokens, certificates, databases, generated board images, or Stockfish binaries.

## Local verification

- On this workstation, do not launch Docker Desktop for local tests. Start the existing DBngin instance with
  `open -a DBngin`; Laravel Herd is not needed for the Python/FastAPI test suite.
- DBngin provides MySQL 8.0.33 at `127.0.0.1:3306` with local user `root` and no password. Create only the disposable
  database `yura_chess_codex_test` and use
  `YURA_CHESS_TEST_DATABASE_URL=mysql+pymysql://root@127.0.0.1:3306/yura_chess_codex_test?charset=utf8mb4`.
- DBngin is a fast local compatibility smoke, not the authoritative database gate. The full local suite should pass
  except for `test_database_is_mariadb_11_4`, which must continue to reject MySQL. CI and release verification still
  require MariaDB 11.4; never weaken or skip that assertion in committed tests.
- Drop only `yura_chess_codex_test` after the run. Never point tests at the development or production database.
- CI gates formatting as well as lint: run `ruff format --check .` next to `ruff check src tests`. A branch that passes
  only the linter still fails the `quality` job.

## Firebat

- Production webhook: `https://yurachess.ru/webhooks/alice`.
- `chess.waxim.ru` permanently redirects all requests to `yurachess.ru`.
- Host nginx owns TLS; the application exposes only a loopback port through an Incus proxy-device.
- Production secrets stay on Firebat with restrictive permissions.
- Production deploys require green CI, an immutable image, health checks, and the public webhook smoke. Backup and
  restore-smoke status is monitored separately and never blocks an application deploy.
- A webhook smoke request needs `session.user.user_id`; the deprecated `session.user_id` alone is answered with
  «Не удалось определить пользователя».
- Every turn after the first must echo the previous reply's `session_state` and `user_state_update` back in
  `request.state`, otherwise each request opens a new game and multi-turn behaviour cannot be observed.

<!-- devkit-toolkit:start -->
## devkit-toolkit

Devkit policy:

- Skill selection starts from the catalog metadata. Activate only the smallest directly relevant set; do not open candidate skills or conduct documents merely to compare them.
- On Claude Code, activation means calling `Skill(<catalog-slug>)` before starting that workflow; mentioning a skill in prose does not activate it.
- A code-changing request requires the coder skill before any edit tool. Claude Code invokes `Skill(devkit-core--coder)`; Codex loads `devkit-coder`. Claude Skill tool arguments use directory slugs such as `devkit-core--coder`, not frontmatter names such as `devkit-coder`.
- A stack/framework architecture or design request not covered by another core workflow uses `Skill(devkit-core--devkit-router)` on Claude Code or `devkit` on Codex.
- After reading the selected `SKILL.md`, begin grounding in the user's target immediately. Load references and conduct only when a specific file, layer, risk, or decision requires them; never scan a conduct directory wholesale.
- Review/test/QA routing follows `plugins/core/conduct/review-routing.md`: `docs/plans/**` uses plan-reviewer; code or a whole branch uses reviewer-deep plus reviewer-business-logic; "test"/"протестируй" uses browser.
- `devkit-plan-creator` and `devkit-plan-reviewer` require the literal token `ralphex`, except that a review target under `docs/plans/**` may invoke plan-reviewer. Claude Code's built-in `/plan` mode invokes neither.
- When a skill applies, state which one and why, then activate it. When none applies, proceed without mentioning skill evaluation.
- Before a top-level final response when completed work may have revealed durable project knowledge, apply
  `~/.claude/agentic-devkit/plugins/core/conduct/learning-capture-gate.md`. When a candidate passes, Claude Code calls
  `Skill(devkit-core--learn)`; Codex/Cursor activate `devkit-learn` through their native skill mechanism. Otherwise finish
  silently. Dispatched subagents never run this gate.

Enabled plugins define eligible conduct, not mandatory context. Follow the loading policy below only when the task
touches that plugin. Start from its index and open further documents only for concrete layers or risks; never read a
conduct directory wholesale.

### devkit-core

- ~/.claude/agentic-devkit/plugins/core/conduct/overview.md

### devkit-frontend

- ~/.claude/agentic-devkit/plugins/frontend/conduct/overview.md
<!-- devkit-toolkit:end -->
