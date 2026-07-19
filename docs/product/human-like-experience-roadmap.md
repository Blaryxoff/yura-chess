# Human-like chess experience roadmap

## Product decision

Keep three explicit modes so help never changes the meaning of an ordinary game:

1. **Game** — the default fair-play mode. Alice plays, answers factual questions and does not volunteer engine advice.
2. **Training** — an opt-in game with evaluation, explanations, candidate moves, graduated hints and takebacks.
3. **Puzzles** — a separate exercise flow with its own position, progress and history; it never mutates a saved game.

Human-like means concise, context-aware and predictable. Alice should not praise every move, hide an engine suggestion inside casual commentary, or require a screen. Voice remains complete; cards remain optional.

## Existing foundation

The current modular monolith already provides the reusable core:

- `CommandRouter` separates controls, questions and legal/illegal/ambiguous moves.
- `ConversationService` owns confirmations, saved-game resume and spoken responses.
- `GameService` and MariaDB preserve owner-isolated games and canonical UCI history, including undo and idempotent Alice replay.
- `python-chess` reconstructs every position and can answer rule-based facts without Stockfish.
- The bounded Stockfish pool supports per-game strength and controlled timeouts.
- Position speech, recent-move history, screen boards and board orientation already work.
- The ASR corpus records normalized commands, so new phrases can be driven by real failures rather than speculative synonyms.

Do not create separate services for these features. Extend the existing application/domain/storage boundaries and keep all Alice responses inside the current webhook budget.

## Voice use cases

### Available in every game

| User says | Expected response |
| --- | --- |
| «Каким цветом я играю?» | Player colour. |
| «Какой сейчас ход?» / «Сколько ходов сыграно?» | Move number, side to move and completed full moves. |
| «Какие фигуры сняты?» | Captured material for both sides, grouped by colour. |
| «Могу ли я рокироваться?» | Remaining short/long castling rights and, when relevant, why castling is unavailable now. |
| «У меня шах?» | Check status and attacking piece or pieces. |
| «Что изменилось после хода?» | Last move, capture/check/promotion and directly changed squares. |
| «Какая стадия партии?» | Opening, middlegame or endgame using a documented deterministic heuristic. |
| «Как называется дебют?» | ECO opening/variation when the move history has a licensed match; otherwise say it is not identified. |
| «Говори короче» / «Объясняй подробнее» | Persisted response-detail preference. |
| «Говори медленнее» / «Говори быстрее» | Persisted TTS pacing preference without changing chess semantics. |
| «Называй обе клетки» / «Короткая нотация» | Persisted move-speech style. |
| «Показывай доску за чёрных» / «Показывай за белых» | Persisted screen orientation; spoken play remains unchanged. |
| «Реванш тем же цветом» / «Поменяемся цветами» | New game with inherited level and explicit colour choice. |
| «Следующую партию сыграй сильнее» | Confirm the next level before starting a new game. |

### Training mode only

| User says | Expected response |
| --- | --- |
| «Включи режим тренера» / «Играй без подсказок» | Switch mode explicitly and confirm the consequences. |
| «Как оценивается позиция?» | Spoken category first: equal, slight/clear advantage or winning; numeric score only on request. |
| «Почему ты так сходила?» | One concrete purpose of Alice's last move, based on the position and principal variation. |
| «Чем ты угрожаешь?» | Immediate forcing threat, or an honest statement that there is no clear tactical threat. |
| «Что будет, если я сыграю конь эф три?» | Analyze the proposed legal move without applying it; explain illegality normally if it is illegal. |
| «Какие у меня хорошие ходы?» | At most three candidates, ordered, without moving a piece. |
| «Дай подсказку» | Graduated hint: idea/theme, then source piece, then destination, then full move. |
| «Где я ошибся?» | Most recent meaningful evaluation drop, not merely the latest non-best move. |
| «Оставить мой ход» / «Вернуть ход» | Resolve any training warning explicitly; never silently replace the user's move. |

### After a game

| User says | Expected response |
| --- | --- |
| «Разбери партию» | Short result, turning point, main mistake and best player move. |
| «Где был перелом?» | Move number, spoken move and practical consequence. |
| «Какая моя главная ошибка?» | The largest meaningful evaluation loss with a short alternative. |
| «Сколько ошибок я сделал?» | Counts by documented thresholds; avoid false precision at low analysis depth. |
| «Сыграть эту позицию заново» | Confirm and start a new training branch from the named turning point. |
| «Продиктуй партию» / «Покажи PGN» | Paginated spoken history; PGN is an optional screen enhancement. |

### Puzzle mode

Support «дай шахматную задачу», «мат в один», «мат в два», «задача на вилку», «задача моего уровня», «повтори позицию», «дай подсказку», «почему решение правильное», «следующая задача» and «какая у меня серия». The detailed catalogue and licensing rules remain in [puzzles-roadmap.md](puzzles-roadmap.md).

## Delivery phases

### Phase 1 — factual questions and preferences

**Priority:** first; useful to every player and mostly independent of Stockfish.

Implementation:

- Add command kinds for game facts, speech preferences, orientation and rematch variants.
- Extend position presentation using `python-chess` and canonical UCI history for colour, move counts, captures, castling, check and last-move changes.
- Add an owner-scoped preferences row with detail level, pace, notation style, board orientation and default mode. Keep Alice state limited to identifiers/replay metadata.
- Add a small licensed ECO lookup keyed by UCI move prefixes. Opening recognition must degrade to «дебют не определён» without affecting play.
- Derive game stage with a deterministic material/development heuristic and test its boundaries.

Dependencies: one Alembic migration for preferences; licensed ECO data with source metadata; no engine API changes.

Acceptance criteria:

- Every phrase in “Available in every game” has voice-only tests with natural variants.
- Preferences survive a new Alice session and do not leak across users.
- All factual answers are identical after reload because they derive from canonical history.
- A preference command never becomes a chess move or mutates the board.
- Existing idempotency, resume, CLI and screen-orientation suites remain green.

### Phase 2 — bounded training mode

**Priority:** second; highest experiential value, but it adds engine and latency risk.

Implementation:

- Persist an explicit mode per active game; default new games to `game` unless the user opts into `training`.
- Extend the engine port from move-only search to a typed, read-only analysis result: score, mate distance, principal variation and top candidates.
- Run analysis through the existing bounded pool, off the event loop, with a shorter controlled deadline and the same busy/timeout fallback. Analysis must never hold a DB transaction.
- Resolve proposed moves through the existing voice resolver, copy the board, analyze the copy and never append the move.
- Implement graduated hints and evaluation-drop thresholds centrally so all spoken explanations use the same rules.
- Cache small analysis summaries by position hash and engine settings; cache misses are safe and disposable.

Dependencies: typed engine-analysis domain object; configuration for thresholds/deadline; optional bounded database cache only after live measurements show repeated analysis load.

Acceptance criteria:

- Every phrase in “Training mode only” is tested for legal, illegal, ambiguous, engine-busy and timeout paths.
- The same phrase in normal game mode offers to enable training instead of revealing advice.
- Questions and proposed-move analysis never change UCI history, revision or pending engine turn.
- A hint reveals exactly one additional level per request and survives Alice request replay idempotently.
- Responses stay within Alice's total webhook budget under pool saturation.

### Phase 3 — restrained commentary and post-game review

**Priority:** third; build only after real training transcripts show the explanations are useful.

Implementation:

- Generate comments only on meaningful events: check, material swing, promotion, phase transition, recognizable opening or large evaluation change.
- Apply a cooldown and the user's detail preference; default game mode gets rule-based facts, not hidden engine coaching.
- Record compact analysis checkpoints during training so a post-game summary does not synchronously analyze every ply in one webhook.
- Page longer reviews across turns and retain the reviewed game/ply in server-side state.
- Export standards-compliant PGN from canonical history; screen display is optional and speech can paginate the moves.

Dependencies: Phase 2 analysis model; storage for per-ply analysis/checkpoints; explicit retention policy for derived analysis.

Acceptance criteria:

- Alice does not comment after ordinary quiet moves and never repeats the same comment category on adjacent turns.
- Turning point and error counts use documented score-loss thresholds and name the analyzed depth/limitations when asked.
- Post-game review resumes after session interruption and cannot alter the finished game.
- PGN round-trips to the same final position and move history.

### Phase 4 — voice puzzles

**Priority:** fourth; separate release milestone after normal and training games are stable.

Implementation:

- Add puzzle catalogue, solution tree and owner-scoped attempt state as described in [puzzles-roadmap.md](puzzles-roadmap.md).
- Reuse normalization, move resolution, legality explanations, position speech and board rendering.
- Use precomputed verified solutions at runtime; Stockfish may validate imports offline but does not choose the expected live answer.
- Keep saved game, training game and puzzle resume prompts distinct.

Dependencies: verified redistributable/self-generated puzzle source; catalogue importer; migrations for puzzles and attempts.

Acceptance criteria:

- Mate-in-one, mate-in-two and short tactical trees cover correct alternatives, legal-but-wrong, illegal and ambiguous moves.
- Closing and reopening Alice resumes the correct puzzle without hiding an unfinished normal game.
- Hint progression, streak, difficulty and abandon/return flows work by voice only.
- Puzzle activity never changes game rows, revisions or UCI histories.

## Cross-cutting test and rollout gates

- Add each new utterance to router, conversation and captured-ASR regression tests before release.
- Exercise every command through the shell runner as well as Alice adapter tests; questions must work without a screen.
- Preserve exact replay responses and conflicting-fingerprint rejection for all new commands.
- Track unknown/repeated commands in the existing privacy-limited transcript corpus and prioritize additions from real frequency.
- Roll out one phase at a time behind a server-side feature flag; do not change the default fair-play behaviour during moderation.
- Run real-device voice acceptance across quiet/noisy speech, white/black orientation and resumed sessions before enabling a phase publicly.

## Explicit deferral: chess clocks

Do not implement live voice chess clocks. Alice device latency, ASR duration, network delay and webhook processing are outside the player's control, so blitz timing would be unreliable and unfair. Reconsider only if Yandex exposes authoritative utterance start/end timestamps and product tests can distinguish thinking time from platform latency. Untimed games and optional non-competitive elapsed-time statistics are sufficient until then.
