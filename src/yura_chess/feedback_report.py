from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date

from yura_chess.settings import get_settings
from yura_chess.storage.database import create_database_engine, create_session_factory, session_scope
from yura_chess.storage.feedback_repository import FeedbackReport, FeedbackRepository


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD")
    return parsed


def _nonnegative(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use a nonnegative integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("use a nonnegative integer")
    return parsed


def format_report(report: FeedbackReport) -> str:
    lines = [
        f"Review engagement: {report.since} through {report.until} (inclusive UTC dates)",
        "Prompts shown in real Alice requests (replays and test traffic excluded):",
        *(f"  {kind}: {count}" for kind, count in report.prompts.items()),
        "Outbound redirect requests (not unique people or submitted reviews):",
        *(f"  {source}: {count}" for source, count in report.clicks.items()),
        "Manually observed cumulative public totals (latest observations on or before report end):",
    ]
    for history in report.published:
        latest, previous = history.latest, history.previous
        if latest is None:
            lines.append(f"  {history.platform}: no observation recorded; baseline unavailable")
            continue
        lines.append(
            f"  {history.platform} on {latest.day}: {latest.ratings} ratings, {latest.reviews} written reviews"
        )
        if previous is None:
            lines.append("    Net change unavailable until a second observation")
        else:
            lines.append(
                f"    Previous on {previous.day}: {previous.ratings} ratings, {previous.reviews} written reviews"
            )
            lines.append(
                f"    Net change: {latest.ratings - previous.ratings:+d} ratings, "
                f"{latest.reviews - previous.reviews:+d} written reviews (may include moderation removals)"
            )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Review engagement and manually observed published totals")
    commands = parser.add_subparsers(dest="command", required=True)
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--since", type=_date, required=True)
    report_parser.add_argument("--until", type=_date, required=True)
    published_parser = commands.add_parser("record-published")
    published_parser.add_argument("--platform", choices=("dialogs", "browser"), required=True)
    published_parser.add_argument("--date", type=_date, required=True)
    published_parser.add_argument("--ratings", type=_nonnegative, required=True)
    published_parser.add_argument("--reviews", type=_nonnegative, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "report" and arguments.since > arguments.until:
        parser.error("--since must be on or before --until")
    if arguments.command == "record-published" and arguments.reviews > arguments.ratings:
        parser.error("--reviews must not exceed --ratings")
    engine = create_database_engine(get_settings())
    try:
        with session_scope(create_session_factory(engine)) as session:
            repository = FeedbackRepository(session)
            if arguments.command == "report":
                print(format_report(repository.report(arguments.since, arguments.until)))
            else:
                repository.record_published(arguments.platform, arguments.date, arguments.ratings, arguments.reviews)
                print(
                    f"Recorded local {arguments.platform} observation for {arguments.date}; nothing submitted to Yandex"
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
