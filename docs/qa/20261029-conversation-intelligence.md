# Conversation intelligence follow-up — 2026-10-29

## Scope and decisions

Investigated the weekly report for 22–28 October against 15–21 October (MSK),
with local HEAD based on `ce93521` and production routing based on `c53952b4`.

- Accept only the captured exact relaunch synonym «включи навык шахматы с юрой».
  Reuse `skill_launch`, preserving the game and all conversation contexts.
- Treat «назад» as contextual navigation. Help and review keep previous-page
  behavior; elsewhere name an explicit command, preserving pending actions and
  move clarification. In an ordinary game, ask whether to undo or continue.
  Never infer an undo from this phrase.
- Preserve review pagination ahead of a concurrently open puzzle. A regression
  test exposed the puzzle intercepting review navigation before this change.
- Do not expand ambiguous ASR fragments, bare piece names, confirmations or
  review-access vocabulary. The report provides no evidence for new features.

## Recording gap investigation

Production checks used SSH to Firebat and its `yura-chess` Incus container.
Database queries ran in read-only transactions ending with `ROLLBACK`.
Only aggregate counts and timestamps were returned; production was not changed.

- Durable real request history ends at 11 October 03:17:04 MSK before resuming
  on 16 October at 20:00:41 MSK. The gap is present in `usage_requests` as well
  as retained ASR, so it is not specific to transcript storage.
- Host nginx logs for `yurachess.ru` contain no `/webhooks/alice` requests on
  13, 14 or 15 October. Other traffic continued, including 63, 107 and 65
  HTTP 200 responses respectively. This does not establish webhook health
  during the gap: no webhook probes were observed.
- On 16 October, nginx recorded 132 HTTP 200 webhook requests, matching the
  132 durable real requests for that day.
- Old-domain logs for 14–15 October contain three non-webhook HTTP 301
  responses and no evidence of webhook requests reaching the old domain.
- ASR retention is 30 days and the retained corpus reaches 29 September.
  Ordinary transcript expiration does not explain the gap.

For the inspected days, requests did not reach host nginx. The evidence cannot
distinguish upstream delivery, skill availability or lack of client requests.
An application recording fix is not justified. Weekly request-volume growth
remains uninterpretable as demand growth because the baseline is incomplete.

## Release measurement

Local validation: the new routing tests first failed for the missing behaviors;
the final focused selection passed 122 cases. The full suite passed 2,536 tests
on DBngin MariaDB 12.3 with 26 opt-in deployed webhook cases skipped. Ruff lint
and format, mypy and Alembic check passed. The disposable test database was
removed afterward. MariaDB 11.4 CI, image build and deployed smoke are still
required before a release; this change has not been deployed.

After deployment, measure the exact relaunch and «назад» clusters separately by
release, including distinct users, new routing outcomes and neighboring false
positives. Keep historical outcomes unchanged. Check help/review pagination and
the absence of game-history changes for «назад». Continue observing review-access
phrases; expand only when a specific formulation has sufficient evidence.
