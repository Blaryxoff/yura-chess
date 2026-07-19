# Whole-app test plan and publication gates

## Goal

Prove that «Шахматы с Юрой» is safe for a public multi-user release: voice-only play remains complete, legal state is
never corrupted, repeated Alice deliveries never duplicate moves, unfinished games resume correctly, screen cards stay
optional, and deployment can be restored after failure.

## Test environments

| Environment | Purpose | Data policy |
| --- | --- | --- |
| Unit process | Pure chess, routing, speech and image functions | No persistent data |
| Disposable MariaDB 11.4 | Clean migration and schema tests | Ephemeral container only |
| Local Compose | Real application, Stockfish and MariaDB smoke | Dedicated `ultratest-*` profiles |
| Firebat staging | Release candidate, timeouts and concurrency | Dedicated pseudonymous test users |
| Alice developer console/devices | Real ASR, TTS, screen card and session lifecycle | Test phrases only; no personal speech |
| Firebat production | Post-deploy health and one synthetic game | Dedicated release-smoke identity |

## Suites

### S01 — Static, packaging and configuration

- `S01-01` Ruff lint and formatting pass.
- `S01-02` strict mypy passes for all runtime modules.
- `S01-03` every Compose file resolves with an immutable image and required profiles.
- `S01-04` Docker image builds, runs as the non-root user and starts on a read-only filesystem.
- `S01-05` dependency vulnerability audit has no known high/critical runtime vulnerability.
- `S01-06` repository diff contains no secrets, `.env`, database files, generated PNGs or Stockfish binaries.

### S02 — Chess rules and canonical state

- `S02-01` normal moves, captures, en passant, both castlings and all four promotions persist as UCI history.
- `S02-02` check, mate, stalemate, insufficient material, 50/75 moves and three/fivefold repetition are correct after
  reconstruction from stored history.
- `S02-03` undo removes one complete player/engine turn and is rejected while the engine turn is pending.
- `S02-04` malformed FEN/history, stale revision and foreign game IDs cannot mutate a game.
- `S02-05` randomized legal games reconstruct to the same FEN after every persisted ply.

### S03 — Voice commands and illegal-move explanations

- `S03-01` each supported move has Russian piece names, coordinates, SAN/UCI-like forms, captures and common ASR
  variants.
- `S03-02` ambiguous and low-confidence phrases never change the board and always request clarification.
- `S03-03` illegal moves identify empty source, wrong side, occupied destination, movement geometry, blocker, check,
  pawn, promotion, en-passant and castling causes.
- `S03-04` control commands always outrank move parsing, including every `где ...` position question.
- `S03-05` empty, very long, punctuation-only, mixed-case and unexpected Unicode input never crashes or leaks details.
- `S03-06` a corpus replay report tracks recognition success by normalized phrase without storing audio or direct Alice IDs.

### S04 — Spoken position, history and accessibility

- `S04-01` answer what is on every square, including an empty square.
- `S04-02` locate every piece kind for White, Black and both sides: for example `где белые слоны`.
- `S04-03` list one side, read the whole board in stable pages and continue without losing the page.
- `S04-04` answer turn, check, last action and N previous actions, optionally filtered by side.
- `S04-05` every coordinate in TTS normalizes back to the original square.
- `S04-06` every required answer is complete without a screen and stays within Alice text/TTS limits.

### S05 — Conversation lifecycle and saved games

- `S05-01` start as either color and every engine level; Black receives exactly one opening engine move.
- `S05-02` a new session offers the latest unfinished game and reads the last two actions.
- `S05-03` accepting resume restores the exact game; declining leaves it untouched and permits a new game.
- `S05-04` opening the skill without making a move does not update the game's played date.
- `S05-05` queries, help, repeats and failed moves do not update last-human-move activity.
- `S05-06` resignation and replacement of an active game require confirmation and survive repeated webhook delivery.

### S06 — Engine resilience and timing

- `S06-01` two Stockfish workers serve independent users concurrently.
- `S06-02` pool exhaustion and search timeout return within the webhook budget and preserve one pending turn.
- `S06-03` retry resumes the pending engine calculation without replaying the player's move.
- `S06-04` a crashed worker is replaced without exposing it as ready prematurely.
- `S06-05` missing Stockfish degrades position/help functions without corrupting games.
- `S06-06` staging load keeps p95 below 4.5 seconds and produces no unbounded queue or process growth.

### S07 — Alice protocol, identity and security

- `S07-01` valid voice-only and screen-capable payloads return protocol-valid responses.
- `S07-02` duplicate `(skill_id, session_id, message_id)` with the same fingerprint is idempotent.
- `S07-03` the same replay key with a different fingerprint is rejected without mutation.
- `S07-04` Alice state is minimal; forged, stale or cross-user `game_id` reveals no foreign game information.
- `S07-05` absent `user_id`, malformed bodies, oversized input and webhook deadline cancellation fail safely.
- `S07-06` logs and transcript rows contain no tokens, full payloads, audio or direct Alice identifiers.

### S08 — Board presentation

- `S08-01` Alice PNG orientation is from the player's side for both White and Black.
- `S08-02` files/ranks, piece placement and last-move highlights are correct in both orientations.
- `S08-03` identical rendered inputs reuse the cache; orientation and last move produce different cache keys.
- `S08-04` Yandex image timeout/quota/error removes only the card, never speech or game state.
- `S08-05` no PNG is written to Firebat and metadata cleanup stays within TTL/LRU bounds.
- `S08-06` CLI board prints coordinates after each game response and can explicitly select White or Black orientation.

### S09 — Persistence, migration, retention and recovery

- `S09-01` a clean MariaDB 11.4 upgrades from base to Alembic head.
- `S09-02` a pre-existing schema upgrades through every migration without foreign-key or collation failure.
- `S09-03` application readiness rejects a schema behind or ahead of the supported revision.
- `S09-04` concurrent writers preserve revision, replay and move ordering under row locks.
- `S09-05` transcript, replay and board-cache retention delete only expired rows.
- `S09-06` backup completes off-host and restore-smoke validates every canonical table and Alembic revision.

### S10 — CLI and scripted end-to-end testing

- `S10-01` `--help` needs no runtime secrets; interactive, `--command` and `--script` modes behave identically.
- `S10-02` persistent profiles isolate users and resume unfinished games across shell processes.
- `S10-03` a scripted game covers move, square query, piece query, history, undo, resignation and new game.
- `S10-04` output includes speech, TTS differences, optional FEN and optional coordinate-labelled board.
- `S10-05` process shutdown always closes Stockfish workers and database connections.

### S11 — Deployment and production operations

- `S11-01` CI passes before image publication; image tag equals the Git commit used to build it.
- `S11-02` staging deploy runs migrations, readiness and external webhook smoke.
- `S11-03` failed application health automatically restores the previous immutable image.
- `S11-04` backup and restore-smoke pass immediately before production cutover.
- `S11-05` production deploy preserves existing unfinished games and transcript retention.
- `S11-06` public webhook returns validation errors rather than nginx 4xx/5xx for reachable malformed requests.

### S12 — Real Alice acceptance

- `S12-01` run at least twenty complete real-ASR games across ordinary, tactical and endgame positions.
- `S12-02` repeat the critical flow on a voice-only device and a screen device.
- `S12-03` verify both player colors, board orientation, TTS coordinates and last-move card updates.
- `S12-04` close Alice, return the same day and another day, then accept and decline resume.
- `S12-05` collect failed normalized phrases and reach at least 95% recognition for supported formulations.
- `S12-06` two users play concurrently without mixed games, delayed duplicate moves or leaked state.

## Automation tiers

| Tier | When | Suites |
| --- | --- | --- |
| Fast PR | Every change | S01 lint/types, pure S02–S08 unit tests, focused regression |
| Full CI | Every push to `main` | All automated S01–S10, clean MariaDB migration, Docker build |
| Nightly soak | Nightly while in beta | randomized S02, corpus S03, concurrency/timing S06, retention S09 |
| Release candidate | Before Firebat production | S01–S11 on staging plus backup/restore |
| Manual acceptance | Before public moderation | S12 on actual Alice devices |

## Public-release gates

Do not submit for public moderation until all are true:

1. The release is committed, pushed and represented by a green CI commit and matching immutable image.
2. All automated suites pass on a clean MariaDB and the release image passes staging smoke.
3. CLI orientation gap `S08-06` is resolved or explicitly accepted as developer-only non-scope.
4. Backup plus restore-smoke passes immediately before cutover.
5. Twenty real-ASR games pass, including voice-only and screen devices, both colors and session resume.
6. No unresolved critical/high correctness, privacy, security or data-loss defect remains.
7. The skill privacy text discloses pseudonymous normalized-command analytics and retention.

