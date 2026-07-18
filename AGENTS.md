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
- Keep a modular monolith and one persistent Stockfish process until measured concurrency requires a pool.
- Keep full game state server-side; Alice state contains only identifiers, revision, and replay metadata.
- Render board images in memory. Any byte cache must be bounded and disposable.
- Do not copy code or vocabulary verbatim from `axtrace/alisa_chess` until licensing is explicit.

## Delivery

- Implement one `### Task N` from the active Ralphex dev plan at a time.
- Tests run automatically after code changes: focused tests first, then the full configured suite.
- Add tests with every voice phrase, state transition, rule diagnostic, and regression.
- Prefer captured Alice ASR transcripts over invented synonyms.
- Never commit `.env`, credentials, Yandex tokens, certificates, databases, generated board images, or Stockfish binaries.

## Firebat

- Production webhook: `https://chess.waxim.ru/alice/webhook`.
- Host nginx owns TLS; the application exposes only a loopback port through an Incus proxy-device.
- Production secrets stay on Firebat with restrictive permissions.

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

Enabled plugins define eligible conduct, not mandatory context. Follow the loading policy below only when the task
touches that plugin. Start from its index and open further documents only for concrete layers or risks; never read a
conduct directory wholesale.

### devkit-core

- ~/.claude/agentic-devkit/plugins/core/conduct/overview.md
<!-- devkit-toolkit:end -->
