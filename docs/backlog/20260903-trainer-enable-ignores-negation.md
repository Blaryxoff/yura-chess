---
worth: yes
where: src/yura_chess/application/command_router.py:993
added: 2026-09-03
---
# «Не будь тренером» / «не тренируй меня» still enable the trainer

Found by Codex while re-reviewing the 2026-09-03 fix pass for `docs/backlog/20260903-project-review.md` Blocking #5.
Out of scope for that fix: #5 was specifically about the bare, unanchored `режим тренера` alternative colliding with
its own negated form («выключи/отключи режим тренера»), which is now anchored. This is a different, pre-existing
alternative in the same `TrainingQuestion.ENABLE` pattern.

## The defect

`_TRAINING_PATTERNS`'s `ENABLE` regex also matches bare `будь тренером` and `тренируй` via `re.search`, with no
negation guard:

```
route("не будь тренером", chess.Board()).training.question   # TrainingQuestion.ENABLE
route("не тренируй меня", chess.Board()).training.question   # TrainingQuestion.ENABLE
```

A player refusing coaching gets it turned on instead — the same class of bug #5 fixed, on different wording.

- Evidence: `src/yura_chess/application/command_router.py:991-997`.

## Suggested direction

Add a negation guard in front of `будь тренером`/`тренируй`, the way #5 anchored `режим тренера`, or move a
negation check ahead of the whole `_TRAINING_PATTERNS` table (check whether other tables have the same gap before
picking one fix site).
