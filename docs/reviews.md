# Review discovery and measurement

The site navigation and footer link to `/reviews`, which explains how to review the Alice skill in
[Yandex Dialogs](https://dialogs.yandex.ru/store/skills/9ec272d2-shahmaty-s-yuroj#ratings).
The guide separately explains website reviews through the Yandex Browser shield. These are two distinct destinations:
website reviews do not increase the skill's Dialogs review count.

Players can ask «Где оставить отзыв?» in the skill. Screen users receive a link; voice-only users hear the website
address and page instructions. Automatic requests follow a finished game with a player move or three clean puzzle
solutions. Repeat visits and replaced or resigned games do not qualify on their own. There are at most two automatic
requests per player, at least 30 days apart, at a new game-result or puzzle-success moment. «Не напоминай мне об отзывах» disables
automatic requests permanently. A direct request for instructions remains available after dismissal.

## What the report measures

- **Prompts shown:** Alice responses containing automatic requests or requested review instructions, grouped separately.
  Marking the existing pseudonymous usage request keeps retries idempotent. Real traffic only is included; accounts
  classified as test traffic are excluded. A prompt is evidence that the response included the instructions, not proof
  that the device spoke them or the player heard them. Historical prompts before this migration have no request-level
  marker and are not reconstructed.
- **Outbound redirect requests:** requests to the review link, grouped by `skill`, `guide`, or `landing`. Storage contains
  only a UTC day, the source, and an aggregate counter. It stores no cookies, IP addresses, raw identifiers, referrers,
  or user agents. Repeat clicks, crawlers, and previews can increase these totals. They are not unique people, and they
  do not confirm that a review was submitted. Failures to write a metric must not prevent opening the Dialogs page.
- **Published totals:** manually observed cumulative counts of ratings and written reviews, recorded separately for
  `dialogs` and `browser`. A rating is not a written review. The report never treats six ratings as six reviews.
  No baseline is assumed until an operator records an observation. No network submission or automatic import occurs.

Date ranges are inclusive UTC dates. Published totals show the latest and previous observations on or before the
report's end date, even when the previous observation is before its start date. Their difference is a net change
between observations, not submissions attributable to the reporting period or to a particular prompt. Moderation or
removal of reviews can produce a negative change. There is no person-level attribution or verified conversion rate.

Daily click buckets have at most three rows per day. Public observations have at most two rows per observation date.
Prompt markers reuse existing usage rows. There is no additional click event ledger or public metrics-write endpoint.

## Weekly workflow

Run these commands in the application environment with its existing `YURA_CHESS_DATABASE_URL` and settings.
First inspect the public Dialogs review section and the website's browser shield. Record the ratings and written
reviews displayed by each platform. If a total cannot be verified, leave that observation absent rather than guessing.
Replace the dates and example counts below with the observed values:

```sh
uv run python -m yura_chess.feedback_report record-published --platform dialogs --date 2026-09-28 --ratings 6 --reviews 2
uv run python -m yura_chess.feedback_report record-published --platform browser --date 2026-09-28 --ratings 0 --reviews 0
uv run python -m yura_chess.feedback_report report --since 2026-09-22 --until 2026-09-28
```

The example numbers are illustrative; they are not an imported production baseline. Re-running `record-published`
for the same date and platform replaces that local observation, which allows correction of a transcription mistake.
The command rejects negative totals or written-review counts greater than rating counts.

Compare prompt counts, outbound requests, and net published changes as independent signals. Rising prompts without
outbound requests suggests the instructions or link need attention. Outbound requests without public growth can
reflect login friction, abandoned forms, bots, moderation delays, or people leaving ratings without text; the report
cannot determine which explanation applies.
