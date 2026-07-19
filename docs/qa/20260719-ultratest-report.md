# Whole-app ultratest report — 2026-07-19

## Verdict

The application core is strong. The initial pass found three release blockers; the dependency and CLI findings were
fixed and reverified in the same release candidate. The exact commit still has to be published and deployed, and real
Alice voice-only/screen acceptance remains required before public moderation.

## Remediation status

- `ULT-01` resolved in the release candidate: FastAPI 0.139.2, Starlette 1.3.1, Pillow 12.3.0 and pytest 9.1.1; runtime
  and full-environment audits report no known vulnerabilities.
- `ULT-03` resolved: the shell now sends Alice's empty new-session request, restores a persistent profile and supports
  `player`, `white` and `black` board orientation. CLI coverage increased from 43% to 64%.
- The remediated suite passes 314 tests, clean MariaDB `0001`–`0005` migration and the hardened Docker runtime smoke.
- `ULT-02` is resolved only by committing, pushing and deploying the exact immutable commit; public Alice-device
  acceptance remains a separate moderation gate.

## Executed checks

| Area | Result | Evidence |
| --- | --- | --- |
| Ruff lint/format | Pass | 63 files formatted; no lint findings |
| Strict mypy | Pass | 34 source files |
| Full MariaDB suite | Pass | 314 tests after remediation |
| Branch coverage | Pass with gaps | 91% initial whole-app total; CLI increased from 43% to 64% |
| Fresh MariaDB 11.4 | Pass | Alembic `0001` through `0005`; `alembic check` clean |
| Compose validation | Pass | local, staging and production profiles |
| Docker image/runtime | Pass | non-root `yura`, read-only root, all capabilities dropped, readiness with 2/2 workers |
| Local HTTP | Pass | live/ready, malformed webhook 422, valid screen payload 200, exact replay byte-identical |
| Public HTTP | Pass | `POST https://chess.waxim.ru/alice/webhook` with `{}` returns 422 |
| Production health | Pass for old release | database ready; Stockfish 2/2 workers |
| Chaos/fuzz | Pass | 10,000 random utterances, 20,000-character input, 100 random game-history replays |
| Alice PNG orientation | Pass | White and Black square geometry verified |
| Runtime dependency audit | Pass after remediation | no known vulnerabilities in runtime or full environment |
| CLI saved-game resume | Pass after remediation | real two-process profile resume and last-two-moves reminder |
| Real Alice ASR/device pass | Not run | requires developer console plus voice-only and screen devices |
| Backup/restore cutover | Not run | run only against the committed release immediately before deployment |

## Initial blocking findings and resolutions

### ULT-01 — Runtime packages have known advisories

The initial constraints resolved Pillow 11.3.0 and Starlette 0.52.1. Resolved by upgrading FastAPI, Starlette, Pillow
and pytest, adding Starlette's current `httpx2` test dependency, regenerating the lock and rerunning the complete release
gate. Both dependency audits now report no known vulnerabilities.

### ULT-02 — The candidate cannot be reproduced or safely promoted

The feature set is still an uncommitted dirty working tree. GitHub `main` remains at `8c30582`, whose CI fails in the
MariaDB migration path fixed by the working tree. Production is healthy but runs
`dcc8210e6634fb1e5b321b7448804d4b5a2d1d41`, which GitHub no longer recognizes as a repository commit. Do not deploy
another source-unmapped image.

### ULT-03 — CLI cannot exercise Alice new-session recovery

The shell previously created a new random session ID but never sent Alice's empty new-session request. Resolved by
bootstrapping every shell process with `is_new_session=True`, carrying the returned conversation state into commands,
and testing a saved-game prompt across two real CLI processes.

## Significant gaps

- Alice PNG and CLI orientation now have explicit White/Black geometry and coordinate assertions.
- No live Yandex image upload was attempted during this pass; local screen requests correctly degraded to speech-only
  without credentials.
- The required twenty complete real-ASR games, both colors, resume across days and voice-only/screen-device acceptance
  are still manual release gates.
- Production backup and restore-smoke must pass immediately before the eventual cutover.

## Risk Probes

### A. First-break

- Trigger: the next operator deploys another locally built tag before the working tree is committed; Actor: release
  operator; Failure: production runs code that cannot be traced, rebuilt or compared to GitHub and rollback provenance
  remains unknown; Why first: the current production tag is already orphaned and the next release is still dirty, so
  this failure exists before new public traffic arrives.

### B. Chaos

- Case: Alice redelivers the same webhook after a timeout; Status: covered (`tests/adapters/test_alice_webhook.py:287`,
  `tests/application/test_game_service.py:187`); Fix: none.
- Case: a user supplies another user's game ID; Status: covered (`tests/adapters/test_alice_webhook.py:254`); Fix: none.
- Case: the image API fails or exceeds its budget; Status: covered (`tests/presentation/test_board_image.py:190`,
  `tests/presentation/test_board_image.py:247`); Fix: none.
- Case: a developer closes and reopens the shell with the same profile; Status: covered (`tests/test_cli.py` plus the
  two-process release smoke); Fix: none.

### C. User-assumption

- Location: CLI startup (`src/yura_chess/cli.py`); Assumption: none remaining for Alice session bootstrap; Missing
  affordance: n/a — the shell now opens a real new session and prints the continue-game prompt.
- Location: CLI board option (`src/yura_chess/cli.py`); Assumption: none remaining for side selection; Missing
  affordance: n/a — `--orientation white|black|player` is available and defaults to the player.
