"""Durable, privacy-safe usage events and aggregate dashboard queries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Literal

from sqlalchemy import case, func, text
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from yura_chess.storage.models import UsageDashboardCacheRow, UsageRequestRow, UsageUserRow

TrafficSource = Literal["real", "test"]
DashboardSource = Literal["real", "test", "all"]
ChartPeriod = Literal["month", "year", "all"]

# Timestamps are stored in UTC; a report is only meaningful on the day boundaries
# its readers live by. Both sides of that shift — the Python bounds and the SQL
# grouping — derive from this one value, or a change to it would move the range
# without moving the buckets inside it.
_MOSCOW_OFFSET = timedelta(hours=3)


def _in_moscow(column: str) -> str:
    """The stored UTC column expressed in reporting time, for grouping and bucketing."""
    return f"DATE_ADD({column}, INTERVAL {int(_MOSCOW_OFFSET.total_seconds())} SECOND)"


_DAY_BUCKET = "DATE({moment})"
_MONTH_BUCKET = "DATE_SUB(DATE({moment}), INTERVAL DAYOFMONTH({moment}) - 1 DAY)"

_ACTION_KINDS = (
    "help",
    "position_query",
    "game_fact",
    "level_query",
    "results",
    "level",
    "training",
    "review",
    "puzzle",
    "feedback",
    "preference",
    "rematch",
    "color_choice",
    "claim_draw",
    "undo",
    "resign",
    "continue",
    "board_setup",
    "screen",
    "orientation",
    "why",
    "persona",
    "navigate_back",
    "move",
)


def _action_filter(alias: str) -> str:
    kinds = ", ".join(f"'{kind}'" for kind in _ACTION_KINDS)
    return (
        f"(({alias}.routing_outcome = 'handled' AND {alias}.command_kind IN ({kinds})"
        f" AND ({alias}.command_kind <> 'move' OR {alias}.resolution_status = 'resolved'))"
        f" OR ({alias}.command_kind = 'illegal_move' AND {alias}.routing_outcome = 'illegal_move'))"
    )


@dataclass(frozen=True, slots=True)
class UsageTotals:
    launches: int
    actions: int
    users: int
    sessions: int
    engaged_games: int
    player_moves: int
    finished_games: int
    puzzle_plays: int


@dataclass(frozen=True, slots=True)
class DailyUsage:
    day: date
    launches: int = 0
    actions: int = 0
    users: int = 0
    new_users: int = 0
    returning_users: int = 0
    sessions: int = 0
    player_moves: int = 0
    engaged_games: int = 0
    puzzle_plays: int = 0


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    source: DashboardSource
    period: ChartPeriod
    generated_at: datetime
    totals: UsageTotals
    daily: tuple[DailyUsage, ...]


class UsageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_request(
        self,
        owner_key: str,
        skill_id: str,
        session_id: str,
        message_id: str,
        source: TrafficSource,
        created_at: datetime,
        release_id: str = "unknown",
        command_kind: str | None = None,
        resolution_status: str | None = None,
        routing_outcome: str | None = None,
    ) -> None:
        """Upsert one user and one idempotent request event without raw identifiers."""
        user = insert(UsageUserRow).values(
            owner_key=owner_key,
            traffic_source=source,
            first_seen_at=created_at,
            last_seen_at=created_at,
        )
        self._session.execute(
            user.on_duplicate_key_update(
                traffic_source=case(
                    (user.inserted.traffic_source == "test", "test"),
                    else_=UsageUserRow.traffic_source,
                ),
                first_seen_at=case(
                    (user.inserted.first_seen_at < UsageUserRow.first_seen_at, user.inserted.first_seen_at),
                    else_=UsageUserRow.first_seen_at,
                ),
                last_seen_at=case(
                    (user.inserted.last_seen_at > UsageUserRow.last_seen_at, user.inserted.last_seen_at),
                    else_=UsageUserRow.last_seen_at,
                ),
            )
        )

        request = insert(UsageRequestRow).values(
            request_key=request_key(skill_id, session_id, message_id),
            owner_key=owner_key,
            session_key=_key(skill_id, session_id),
            release_id=release_id[:128],
            command_kind=command_kind,
            resolution_status=resolution_status,
            routing_outcome=routing_outcome,
            created_at=created_at,
        )
        self._session.execute(
            request.on_duplicate_key_update(
                release_id=case(
                    (request.inserted.release_id != "unknown", request.inserted.release_id),
                    else_=UsageRequestRow.release_id,
                ),
                command_kind=func.coalesce(request.inserted.command_kind, UsageRequestRow.command_kind),
                resolution_status=func.coalesce(
                    request.inserted.resolution_status,
                    UsageRequestRow.resolution_status,
                ),
                routing_outcome=func.coalesce(request.inserted.routing_outcome, UsageRequestRow.routing_outcome),
                created_at=case(
                    (request.inserted.created_at < UsageRequestRow.created_at, request.inserted.created_at),
                    else_=UsageRequestRow.created_at,
                ),
            )
        )

    def claim_review_prompt(self, owner_key: str, claimed_at: datetime | None = None) -> bool:
        now = claimed_at or datetime.now(UTC).replace(tzinfo=None)
        result = self._session.execute(
            text(
                """
                UPDATE usage_users u
                SET u.review_prompted_at = :claimed_at,
                    u.review_prompt_count = u.review_prompt_count + 1
                WHERE u.owner_key = :owner_key
                  AND u.traffic_source = 'real'
                  AND u.review_prompts_disabled = 0
                  AND u.review_prompt_count < 2
                  AND (u.review_prompted_at IS NULL OR u.review_prompted_at <= :cutoff)
                  AND (
                    EXISTS (
                      SELECT 1
                      FROM games g
                      JOIN game_moves m ON m.game_id = g.id AND m.actor = 'player'
                      WHERE g.owner_key = u.owner_key
                        AND g.status = 'finished'
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM puzzle_profiles p
                      WHERE p.owner_key = u.owner_key
                        AND p.clean_streak >= 3
                    )
                  )
                """
            ),
            {"owner_key": owner_key, "claimed_at": now, "cutoff": now - timedelta(days=30)},
        )
        return int(getattr(result, "rowcount", 0)) == 1

    def disable_review_prompts(self, owner_key: str) -> None:
        self._session.execute(
            text("UPDATE usage_users SET review_prompts_disabled = 1 WHERE owner_key = :owner_key"),
            {"owner_key": owner_key},
        )

    def dashboard(
        self,
        source: DashboardSource,
        now: datetime | None = None,
        period: ChartPeriod = "month",
    ) -> DashboardSnapshot:
        if now is None:
            return self.cached_dashboard(source, period=period)
        generated_at = now or datetime.utcnow()
        reporting_day = _moscow_day(generated_at)
        chart = (
            self._daily(source, reporting_day - timedelta(days=29), 30)
            if period == "month"
            else self._monthly(source, reporting_day, limited=period == "year")
        )
        return DashboardSnapshot(
            source=source,
            period=period,
            generated_at=generated_at,
            totals=self._totals(source, _period_cutoff(generated_at, period)),
            daily=chart,
        )

    def cached_dashboard(
        self, source: DashboardSource, now: datetime | None = None, period: ChartPeriod = "month"
    ) -> DashboardSnapshot:
        generated_at = now or datetime.now(UTC).replace(tzinfo=None)
        reporting_day = _moscow_day(generated_at)
        history = self._history(source, period, reporting_day)
        current = self._incremental_totals(source, _utc_start(reporting_day), _period_cutoff(generated_at, period))
        totals = UsageTotals(**{name: history.totals[name] + value for name, value in asdict(current).items()})
        buckets = {date.fromisoformat(day): values for day, values in history.buckets.items()}
        end_month = reporting_day.replace(day=1)
        live_start = reporting_day if period == "month" else end_month
        buckets.update(self._buckets(source, _DAY_BUCKET if period == "month" else _MONTH_BUCKET, live_start))
        points: list[DailyUsage] = []
        if period == "month":
            days = (reporting_day - timedelta(days=29 - offset) for offset in range(30))
            points = [DailyUsage(day=day, **buckets.get(day, {})) for day in days]
        else:
            month = _add_months(end_month, -11) if period == "year" else min(buckets, default=end_month)
            while month <= end_month:
                points.append(DailyUsage(day=month, **buckets.get(month, {})))
                month = _add_months(month, 1)
        return DashboardSnapshot(source, period, generated_at, totals, tuple(points))

    def _history(self, source: DashboardSource, period: ChartPeriod, reporting_day: date) -> UsageDashboardCacheRow:
        cached = self._session.get(UsageDashboardCacheRow, (source, period), populate_existing=True)
        if cached is not None and cached.reporting_day == reporting_day and cached.schema_version == 1:
            return cached
        cutoff = _period_cutoff(datetime.combine(reporting_day, time.min), period)
        historical_totals = asdict(self._totals(source, cutoff, before=_utc_start(reporting_day)))
        historical_totals["finished_games"] = 0
        month = reporting_day.replace(day=1)
        start = (
            reporting_day - timedelta(days=29)
            if period == "month"
            else _add_months(month, -11)
            if period == "year"
            else None
        )
        closed_before = _utc_start(reporting_day if period == "month" else month)
        buckets = self._buckets(
            source, _DAY_BUCKET if period == "month" else _MONTH_BUCKET, start, before=closed_before
        )
        values = {
            "reporting_day": reporting_day,
            "schema_version": 1,
            "totals": historical_totals,
            "buckets": {day.isoformat(): values for day, values in buckets.items()},
        }
        statement = insert(UsageDashboardCacheRow).values(source=source, period=period, **values)
        self._session.execute(statement.on_duplicate_key_update(**values))
        if cached is not None:
            self._session.expire(cached)
        return UsageDashboardCacheRow(source=source, period=period, **values)

    def _incremental_totals(self, source: DashboardSource, today: datetime, cutoff: datetime | None) -> UsageTotals:
        source_filter = "" if source == "all" else " AND u.traffic_source = :source"
        previous_source = "" if source == "all" else " AND prior_u.traffic_source = :source"
        previous_time = "" if cutoff is None else " AND prior.created_at >= :cutoff"
        finish_time = "" if cutoff is None else " AND g.updated_at >= :cutoff"
        action = _action_filter("r")
        prior_action = _action_filter("prior")
        statement = text(
            f"""
            SELECT
              (SELECT COUNT(DISTINCT r.session_key)
               FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE r.created_at >= :today{source_filter} AND NOT EXISTS (
                 SELECT 1 FROM usage_requests prior JOIN usage_users prior_u ON prior_u.owner_key = prior.owner_key
                 WHERE prior.session_key = r.session_key AND prior.created_at < :today{previous_source}
               )) AS launches,
              (SELECT COUNT(*) FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE r.created_at >= :today AND {action}{source_filter}) AS actions,
              (SELECT COUNT(DISTINCT r.owner_key) FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE r.created_at >= :today AND {action}{source_filter} AND NOT EXISTS (
                 SELECT 1 FROM usage_requests prior WHERE prior.owner_key = r.owner_key
                 AND prior.created_at < :today{previous_time} AND {prior_action}
               )) AS users,
              (SELECT COUNT(DISTINCT r.session_key)
               FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE r.created_at >= :today AND {action}{source_filter} AND NOT EXISTS (
                 SELECT 1 FROM usage_requests prior JOIN usage_users prior_u ON prior_u.owner_key = prior.owner_key
                 WHERE prior.session_key = r.session_key AND prior.created_at < :today
                 {previous_time}{previous_source} AND {prior_action}
               )) AS sessions,
              (SELECT COUNT(DISTINCT g.id) FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
               JOIN game_moves m ON m.game_id = g.id AND m.actor = 'player'
               WHERE m.created_at >= :today{source_filter} AND NOT EXISTS (
                 SELECT 1 FROM game_moves prior WHERE prior.game_id = g.id AND prior.actor = 'player'
                 AND prior.created_at < :today{previous_time}
               )) AS engaged_games,
              (SELECT COUNT(*) FROM game_moves m JOIN games g ON g.id = m.game_id
               JOIN usage_users u ON u.owner_key = g.owner_key
               WHERE m.actor = 'player' AND m.created_at >= :today{source_filter}) AS player_moves,
              (SELECT COUNT(*) FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
               WHERE g.status IN ('finished', 'resigned'){source_filter}{finish_time}) AS finished_games,
              (SELECT COUNT(*) FROM puzzle_attempts p JOIN usage_users u ON u.owner_key = p.owner_key
               WHERE p.created_at >= :today{source_filter}) AS puzzle_plays
            """
        )
        parameters: dict[str, object] = {"today": today}
        if source != "all":
            parameters["source"] = source
        if cutoff is not None:
            parameters["cutoff"] = cutoff
        row = self._session.execute(statement, parameters).mappings().one()
        return UsageTotals(**{name: int(row[name]) for name in UsageTotals.__dataclass_fields__})

    def _totals(
        self, source: DashboardSource, cutoff: datetime | None, *, before: datetime | None = None
    ) -> UsageTotals:
        source_filter = "" if source == "all" else " AND u.traffic_source = :source"

        def window(column: str) -> str:
            lower = "" if cutoff is None else f" AND {column} >= :cutoff"
            upper = "" if before is None else f" AND {column} < :before"
            return lower + upper

        request_time = window("r.created_at")
        launch_time = " WHERE 1=1" + window("s.launched_at")
        move_time = window("m.created_at")
        finish_time = window("g.updated_at")
        puzzle_time = window("p.created_at")
        action = _action_filter("r")
        statement = text(
            f"""
            SELECT
              (SELECT COUNT(*) FROM (
                 SELECT r.session_key, MIN(r.created_at) AS launched_at
                 FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
                 WHERE 1=1{source_filter} GROUP BY r.session_key
               ) s{launch_time}) AS launches,
              (SELECT COUNT(*) FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE {action}{source_filter}{request_time}) AS actions,
              (SELECT COUNT(DISTINCT r.owner_key) FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE {action}{source_filter}{request_time}) AS users,
              (SELECT COUNT(DISTINCT r.session_key)
               FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE {action}{source_filter}{request_time}) AS sessions,
              (SELECT COUNT(DISTINCT g.id) FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
               JOIN game_moves m ON m.game_id = g.id AND m.actor = 'player'
               WHERE 1=1{source_filter}{move_time}) AS engaged_games,
              (SELECT COUNT(*) FROM game_moves m JOIN games g ON g.id = m.game_id
               JOIN usage_users u ON u.owner_key = g.owner_key
               WHERE m.actor = 'player'{source_filter}{move_time}) AS player_moves,
              (SELECT COUNT(*) FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
               WHERE g.status IN ('finished', 'resigned'){source_filter}{finish_time}) AS finished_games,
              (SELECT COUNT(*) FROM puzzle_attempts p JOIN usage_users u ON u.owner_key = p.owner_key
               WHERE 1=1{source_filter}{puzzle_time}) AS puzzle_plays
            """
        )
        parameters: dict[str, object] = {}
        if source != "all":
            parameters["source"] = source
        if cutoff is not None:
            parameters["cutoff"] = cutoff
        if before is not None:
            parameters["before"] = before
        row = self._session.execute(statement, parameters).mappings().one()
        return UsageTotals(**{field: int(row[field]) for field in UsageTotals.__dataclass_fields__})

    def _buckets(
        self, source: DashboardSource, bucket: str, start: date | None, *, before: datetime | None = None
    ) -> dict[date, dict[str, int]]:
        """Every charted metric grouped by the given time bucket, keyed by its first day."""
        source_filter = "" if source == "all" else " AND u.traffic_source = :source"
        parameters: dict[str, object] = {}
        if source != "all":
            parameters["source"] = source
        if start is not None:
            parameters["start"] = _utc_start(start)
        if before is not None:
            parameters["before"] = before
        buckets: dict[date, dict[str, int]] = {}

        def collect(column: str, tables: str, condition: str, metrics: str) -> None:
            time_filter = "" if start is None else f" AND {column} >= :start"
            if before is not None:
                time_filter += f" AND {column} < :before"
            rows = self._session.execute(
                text(
                    f"""
                    SELECT {bucket.format(moment=_in_moscow(column))} bucket, {metrics}
                    FROM {tables}
                    WHERE 1=1{condition}{source_filter}{time_filter}
                    GROUP BY bucket
                    """
                ),
                parameters,
            ).mappings()
            for row in rows:
                values = buckets.setdefault(row["bucket"], {})
                values.update({name: int(count) for name, count in row.items() if name != "bucket"})
                if "users" in row:
                    values["returning_users"] = values["users"] - values["new_users"]

        active_bucket = bucket.format(moment=_in_moscow("r.created_at"))
        previous_action = (
            "SELECT 1 FROM usage_requests r0 WHERE r0.owner_key = r.owner_key "
            f"AND r0.created_at < DATE_SUB({active_bucket}, INTERVAL {int(_MOSCOW_OFFSET.total_seconds())} SECOND) "
            f"AND {_action_filter('r0')}"
        )
        collect(
            "r.created_at",
            "usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key",
            f" AND {_action_filter('r')}",
            "COUNT(*) actions, COUNT(DISTINCT r.owner_key) users, COUNT(DISTINCT r.session_key) sessions,"
            f" COUNT(DISTINCT CASE WHEN NOT EXISTS ({previous_action}) THEN r.owner_key END) new_users",
        )
        collect(
            "r.created_at",
            "usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key",
            " AND NOT EXISTS (SELECT 1 FROM usage_requests r0 WHERE r0.session_key = r.session_key"
            " AND r0.owner_key = r.owner_key AND r0.created_at < r.created_at)",
            "COUNT(DISTINCT r.session_key, r.owner_key) launches",
        )
        collect(
            "m.created_at",
            "game_moves m JOIN games g ON g.id = m.game_id JOIN usage_users u ON u.owner_key = g.owner_key",
            " AND m.actor = 'player'",
            "COUNT(*) player_moves, COUNT(DISTINCT g.id) engaged_games",
        )
        collect(
            "p.created_at",
            "puzzle_attempts p JOIN usage_users u ON u.owner_key = p.owner_key",
            "",
            "COUNT(*) puzzle_plays",
        )
        return buckets

    def _daily(self, source: DashboardSource, start: date, day_count: int) -> tuple[DailyUsage, ...]:
        buckets = self._buckets(source, _DAY_BUCKET, start)
        days = (start + timedelta(days=offset) for offset in range(day_count))
        return tuple(DailyUsage(day=day, **buckets.get(day, {})) for day in days)

    def _monthly(self, source: DashboardSource, end: date, *, limited: bool) -> tuple[DailyUsage, ...]:
        end_month = date(end.year, end.month, 1)
        start = _add_months(end_month, -11) if limited else None
        buckets = self._buckets(source, _MONTH_BUCKET, start)
        months: list[DailyUsage] = []
        month = start or min(buckets, default=end_month)
        while month <= end_month:
            months.append(DailyUsage(day=month, **buckets.get(month, {})))
            month = _add_months(month, 1)
        return tuple(months)


def _key(*parts: str) -> str:
    return sha256("\0".join(parts).encode()).hexdigest()


def request_key(skill_id: str, session_id: str, message_id: str) -> str:
    """Return the privacy-safe durable key shared by usage and retained transcripts."""
    return _key(skill_id, session_id, message_id)


def _add_months(value: date, count: int) -> date:
    month_index = value.year * 12 + value.month - 1 + count
    return date(month_index // 12, month_index % 12 + 1, 1)


def _period_cutoff(generated_at: datetime, period: ChartPeriod) -> datetime | None:
    if period == "all":
        return None
    reporting_day = _moscow_day(generated_at)
    start = reporting_day - timedelta(days=29) if period == "month" else _add_months(reporting_day.replace(day=1), -11)
    return _utc_start(start)


def _moscow_day(utc_value: datetime) -> date:
    return (utc_value + _MOSCOW_OFFSET).date()


def _utc_start(moscow_day: date) -> datetime:
    return datetime.combine(moscow_day, time.min) - _MOSCOW_OFFSET
