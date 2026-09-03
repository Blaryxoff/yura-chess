---
worth: yes
where: src/yura_chess/storage/database.py:43
added: 2026-09-03
---
# Synchronous DB calls on the event loop defeat the webhook and engine deadlines

Split out of `docs/backlog/20260903-project-review.md` Blocking #4. Deferred from the 2026-09-03 fix pass because
it is an architecture-scale change, not a patch: Codex's validated implementation strategy requires converting the
whole application entry-point layer (`game_service.py`, `puzzle_service.py`, `review_service.py`,
`training_service.py`, `conversation.py`, `webhook.py`) from synchronous to async DB access, not a lexical guard.

## The defect

`create_database_engine` returns a synchronous SQLAlchemy `Engine`; `session_scope` is a plain `@contextmanager`.
39 `session_scope(...)` call sites in `src/yura_chess/application/` (plus 2 more via
`run_transaction_with_deadlock_retry`) run directly inside `async def` coroutines — `ConversationService.handle`
calls one as its very first act (`src/yura_chess/application/conversation.py:230`). Zero `run_in_threadpool` calls
exist anywhere in `application/`; all 9 in the codebase are confined to `main.py`, `adapters/yandex_images.py` and
`engine/stockfish.py`.

The two deadlines the architecture depends on are event-loop timers: `asyncio.timeout(webhook_deadline_seconds)`
in `src/yura_chess/adapters/alice/webhook.py:113` and `asyncio.wait_for(..., engine_acquire_timeout_seconds)` in
`src/yura_chess/engine/stockfish.py:304`. A blocking socket read on the loop defers timer callbacks, so under
concurrency one request's blocking DB call can prevent another request's 4.5 s webhook budget or 0.5 s engine-acquire
timeout from firing on time. Full evidence and Codex's confirmation: `docs/backlog/20260903-project-review.md`
Blocking #4.

## Recommended implementation (Codex-validated, 2026-09-03)

Keep the synchronous driver and repositories. Add one typed async transaction helper in `storage/database.py` that
accepts a callback and runs the entire `session_scope`/deadlock-retry operation inside `run_in_threadpool`. Do
**not** expose an async context manager yielding a `Session` — a SQLAlchemy session must not cross threads or
survive an `await`. The helper must return only domain dataclasses, primitives, or fully materialized collections.

Convert application DB-facing methods to `async def` and `await` them through `ConversationService` and the
webhook. Preserve deadlock retry for `_save_preference` and `PuzzleService.answer` via a helper option or an async
companion to the existing retry runner. Consider a stronger guard once converted: application modules may not
import or call `session_scope` or the synchronous retry runner directly.

Call sites needing special care (do not hold a session while awaiting the Stockfish engine):

- `GameService.start_game`, `continue_game`, `resume_request`, `play_move`, `_play_engine_move` — preserve the
  transaction-A → engine-await → transaction-B split (`game_service.py:130`, `:498`).
- `TrainingService.observe_player_move`, `_hint` — keep engine analysis async, offload only the checkpoint/hint-stage
  transactions (`training_service.py:120`, `:300`).
- `ReviewService.start_branch`, `_valuations`, `_value_ply` — separate cached DB reads/writes from the analysis
  awaits (`review_service.py:124`, `:237`).
- Puzzle operations (`find_open`, `replayed_response`, `answer`, `play`, `hint`, `abandon`) are synchronous today but
  called from async conversation handling; convert together.
- Webhook replay lookup and response-cache writes (`webhook.py:210`, `:129`).

Because cancellation does not stop an already-running worker thread, request claims, optimistic revisions, and
idempotent finalization must stay correct inside each transaction after the conversion. Add a concurrency test with
an intentionally delayed DB operation proving the event loop's heartbeat and the webhook timeout still fire on time.

## Why this is worth doing, why it was deferred

This is the correctness backbone for the "at most 3 s" and "4.5 s webhook budget" product invariants under real
concurrency — worth: yes, not later. It was deferred rather than folded into the 2026-09-03 fix pass because its
diff (~40 call sites across 6 files, async/await threaded through the whole request path) is architecture-scale, and
folding it into a mixed batch with eight small blocking fixes would make the batch impossible to review or roll back
independently. Give it its own change and its own full-suite verification pass.
