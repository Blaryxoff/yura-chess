from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from yura_chess.storage.models import (
    PublishedReviewSnapshotRow,
    ReviewClickDailyRow,
    UsageRequestRow,
    UsageUserRow,
)

PromptKind = Literal["automatic", "requested"]
ClickSource = Literal["skill", "guide", "landing"]
ReviewPlatform = Literal["dialogs", "browser"]


@dataclass(frozen=True, slots=True)
class PublishedReviewSnapshot:
    day: date
    ratings: int
    reviews: int


@dataclass(frozen=True, slots=True)
class PublishedReviewHistory:
    platform: ReviewPlatform
    latest: PublishedReviewSnapshot | None
    previous: PublishedReviewSnapshot | None


@dataclass(frozen=True, slots=True)
class FeedbackReport:
    since: date
    until: date
    prompts: dict[PromptKind, int]
    clicks: dict[ClickSource, int]
    published: tuple[PublishedReviewHistory, ...]


class FeedbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_prompt(self, owner_key: str, request_key: str, kind: PromptKind) -> bool:
        if kind not in {"automatic", "requested"}:
            raise ValueError("invalid review prompt kind")
        result = self._session.execute(
            update(UsageRequestRow)
            .where(
                UsageRequestRow.owner_key == owner_key,
                UsageRequestRow.request_key == request_key,
                UsageRequestRow.review_prompt_kind.is_(None),
            )
            .values(review_prompt_kind=kind)
        )
        return int(getattr(result, "rowcount", 0)) == 1

    def record_click(self, source: ClickSource, clicked_at: datetime | None = None) -> None:
        if source not in {"skill", "guide", "landing"}:
            raise ValueError("invalid review click source")
        moment = clicked_at or datetime.now(UTC)
        day = moment.astimezone(UTC).date() if moment.tzinfo is not None else moment.date()
        statement = insert(ReviewClickDailyRow).values(day=day, source=source, clicks=1)
        self._session.execute(statement.on_duplicate_key_update(clicks=ReviewClickDailyRow.clicks + 1))

    def record_published(self, platform: ReviewPlatform, day: date, ratings: int, reviews: int) -> None:
        if platform not in {"dialogs", "browser"}:
            raise ValueError("invalid review platform")
        if ratings < 0 or reviews < 0 or reviews > ratings:
            raise ValueError("published totals require 0 <= reviews <= ratings")
        statement = insert(PublishedReviewSnapshotRow).values(
            platform=platform,
            day=day,
            ratings=ratings,
            reviews=reviews,
        )
        self._session.execute(statement.on_duplicate_key_update(ratings=ratings, reviews=reviews))

    def report(self, since: date, until: date) -> FeedbackReport:
        if since > until:
            raise ValueError("since must be on or before until")
        start = datetime.combine(since, time.min)
        end = datetime.combine(until + timedelta(days=1), time.min)
        prompts: dict[PromptKind, int] = {"automatic": 0, "requested": 0}
        prompt_rows = self._session.execute(
            select(UsageRequestRow.review_prompt_kind, func.count())
            .join(UsageUserRow, UsageUserRow.owner_key == UsageRequestRow.owner_key)
            .where(
                UsageUserRow.traffic_source == "real",
                UsageRequestRow.created_at >= start,
                UsageRequestRow.created_at < end,
                UsageRequestRow.review_prompt_kind.is_not(None),
            )
            .group_by(UsageRequestRow.review_prompt_kind)
        )
        for kind, count in prompt_rows:
            if kind in prompts:
                prompts[kind] = int(count)
        clicks: dict[ClickSource, int] = {"skill": 0, "guide": 0, "landing": 0}
        click_rows = self._session.execute(
            select(ReviewClickDailyRow.source, func.sum(ReviewClickDailyRow.clicks))
            .where(ReviewClickDailyRow.day >= since, ReviewClickDailyRow.day <= until)
            .group_by(ReviewClickDailyRow.source)
        )
        for source, count in click_rows:
            if source in clicks:
                clicks[source] = int(count)
        published = (self._published_history("dialogs", until), self._published_history("browser", until))
        return FeedbackReport(since=since, until=until, prompts=prompts, clicks=clicks, published=published)

    def _published_history(self, platform: ReviewPlatform, until: date) -> PublishedReviewHistory:
        rows = self._session.scalars(
            select(PublishedReviewSnapshotRow)
            .where(PublishedReviewSnapshotRow.platform == platform, PublishedReviewSnapshotRow.day <= until)
            .order_by(PublishedReviewSnapshotRow.day.desc())
            .limit(2)
        ).all()
        snapshots = [PublishedReviewSnapshot(day=row.day, ratings=row.ratings, reviews=row.reviews) for row in rows]
        return PublishedReviewHistory(
            platform=platform,
            latest=snapshots[0] if snapshots else None,
            previous=snapshots[1] if len(snapshots) > 1 else None,
        )
