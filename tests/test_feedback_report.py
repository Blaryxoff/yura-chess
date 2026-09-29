from __future__ import annotations

from datetime import date

import pytest

from yura_chess.feedback_report import format_report, main
from yura_chess.storage.feedback_repository import FeedbackReport, PublishedReviewHistory, PublishedReviewSnapshot


def test_report_output_distinguishes_ratings_reviews_and_net_changes() -> None:
    report = FeedbackReport(
        since=date(2026, 9, 22),
        until=date(2026, 9, 28),
        prompts={"automatic": 4, "requested": 2},
        clicks={"skill": 2, "guide": 1, "landing": 1},
        published=(
            PublishedReviewHistory(
                platform="dialogs",
                latest=PublishedReviewSnapshot(date(2026, 9, 28), 6, 2),
                previous=PublishedReviewSnapshot(date(2026, 9, 21), 7, 3),
            ),
            PublishedReviewHistory(platform="browser", latest=None, previous=None),
        ),
    )

    output = format_report(report)

    assert "inclusive UTC dates" in output
    assert "not unique people or submitted reviews" in output
    assert "6 ratings, 2 written reviews" in output
    assert "Net change: -1 ratings, -1 written reviews (may include moderation removals)" in output
    assert "browser: no observation recorded; baseline unavailable" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["report", "--since", "2026-09-29", "--until", "2026-09-28"],
        ["report", "--since", "20260928", "--until", "2026-09-28"],
        ["record-published", "--platform", "dialogs", "--date", "2026-09-28", "--ratings", "1", "--reviews", "2"],
        ["record-published", "--platform", "browser", "--date", "2026-09-28", "--ratings", "-1", "--reviews", "0"],
    ],
)
def test_cli_rejects_invalid_inputs_before_loading_environment(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)

    assert error.value.code == 2
