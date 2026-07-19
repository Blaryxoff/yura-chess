# Voice chess puzzles — next product milestone

## Decision

Chess puzzles fit the product well, but they should be a separate mode delivered after the current game release is stable. Mixing them into the present deployment would add new state, commands and acceptance paths immediately before publication.

## Voice flow

1. The user says «решать задачи» or «дай шахматную задачу».
2. Alice announces the side to move, goal and difficulty, then offers to read the position.
3. The user can ask the same position questions as in a game: square contents, piece locations, whole position and repetition.
4. The user names a move. The skill validates legality and compares it with the puzzle solution tree.
5. A correct move advances the opponent reply; an incorrect move leaves the position unchanged and gives a short explanation or an optional hint.
6. Alice announces completion, attempt count and the next available action: another puzzle, harder/easier, repeat or return to a normal game.

## First puzzle release

- Mate in one and mate in two.
- Short tactical puzzles with a forced solution tree.
- Difficulty selection and «случайная задача».
- Voice-first position reading and optional screen board.
- Hints: side to move, tactical theme, source piece or destination square.
- Separate puzzle attempt history; no mixing with ordinary saved games.

## Architecture

- Add a `Puzzle` catalogue with initial FEN, side to move, solution variations, theme, difficulty and source/license metadata.
- Add a `PuzzleAttempt` owned by the same pseudonymous user identity, storing current solution node, mistakes, hints and completion time.
- Add an explicit conversation mode so move routing is shared but game and puzzle state cannot mutate each other.
- Validate moves with `python-chess`; use precomputed solution trees for correctness. Stockfish may explain or verify imported content offline, but should not invent the expected move during a live request.
- Import only self-generated puzzles or a dataset whose redistribution license has been verified and recorded per puzzle source.

## Required test additions

- Every solution branch, alternative correct move and forced opponent reply.
- Illegal, legal-but-wrong and ambiguous spoken moves without position mutation.
- Hint progression, retry, completion and abandoning a puzzle for a saved game.
- Resume after a closed Alice session without confusing the puzzle with the latest unfinished game.
- Voice-only and screen-device acceptance for both board orientations.

Create a dedicated product/dev plan before implementation; this is a post-release feature, not part of the current release candidate.

