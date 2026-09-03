---
worth: yes
where: tests/presentation/test_board_image.py:180
added: 2026-09-03
---
# Screen cards are never checked against speech for extra information

Split out of `docs/backlog/20260903-project-review.md` Blocking #9. The 2026-09-03 fix pass replaced the
non-falsifiable `test_speech_is_identical_with_and_without_a_screen` with
`test_card_for_dispatch_also_withholds_the_board_card_without_a_screen`, which correctly verifies that
`_card_for` returns `None` without a screen and a card with one — but that only re-covers ground already held by
`test_no_card_without_a_screen` / `test_card_for_a_screen_capable_request` in the same file. The invariant's real
half is still untested.

## The defect

The product invariant is: a screen card must never carry information absent from the spoken answer (a voice-only
device must get the complete answer from speech alone). No test in the suite compares one turn's card content
against that same turn's speech text for the board card, the PGN `TextCard`, or the puzzle card. The only
comparison that exists is the help card's topic-title check added in this same fix pass
(`tests/adapters/test_alice_webhook.py`, `test_a_help_card_is_optional_and_the_voice_answer_is_unchanged`), which
covers one of four card-producing paths.

- Evidence: `tests/presentation/test_board_image.py:180-183` (post-fix); `src/yura_chess/presentation/response_composer.py:56-61`
  (`compose_board_card`, `compose_pgn_card`, `compose_position_card`); `tests/adapters/test_alice_webhook.py` (help-only check).

## Why deferred

Verifying "card ⊆ speech" for the board/PGN/puzzle cards requires deciding, per card type, what "the information in
the card" even means structurally (a board image has no text to diff against speech at all — the invariant for it
is closer to "the image state matches the FEN the speech implies" than a text-subset check). That is a design
question about what each card type is allowed to assert, not a mechanical test-writing task, so it does not belong
in a fix pass scoped to closing blocking findings.

## Suggested direction

One parametrized test per card-producing reply type:
- Board card: assert the rendered position matches the FEN/move the speech describes (via `position_hash` or
  equivalent), not a text comparison.
- PGN `TextCard` / puzzle card: assert every fact in the card text (move list, puzzle prompt) also appears in the
  speech text, the way the help-card fix now does for topic titles.
- Explicitly exempt anything structurally screen-only by design (e.g. a review button with no spoken equivalent),
  naming the exemption in the test so it reads as a decision, not an oversight.
