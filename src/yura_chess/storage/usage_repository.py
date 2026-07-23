"""Durable, privacy-safe usage events and aggregate dashboard queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Literal

from sqlalchemy import case, text
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from yura_chess.storage.models import UsageRequestRow, UsageUserRow

TrafficSource = Literal["real", "test"]
DashboardSource = Literal["real", "test", "all"]


@dataclass(frozen=True, slots=True)
class UsageTotals:
    requests: int
    users: int
    sessions: int
    games: int
    engaged_games: int
    player_moves: int
    finished_games: int
    puzzle_attempts: int


@dataclass(frozen=True, slots=True)
class DailyUsage:
    day: date
    requests: int = 0
    users: int = 0
    sessions: int = 0
    games: int = 0
    player_moves: int = 0


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    source: DashboardSource
    generated_at: datetime
    last_24_hours: UsageTotals
    last_7_days: UsageTotals
    all_time: UsageTotals
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
            request_key=_key(skill_id, session_id, message_id),
            owner_key=owner_key,
            session_key=_key(skill_id, session_id),
            created_at=created_at,
        )
        self._session.execute(request.on_duplicate_key_update(request_key=request.inserted.request_key))

    def dashboard(self, source: DashboardSource, now: datetime | None = None) -> DashboardSnapshot:
        generated_at = now or datetime.utcnow()
        return DashboardSnapshot(
            source=source,
            generated_at=generated_at,
            last_24_hours=self._totals(source, generated_at - timedelta(days=1)),
            last_7_days=self._totals(source, generated_at - timedelta(days=7)),
            all_time=self._totals(source, None),
            daily=self._daily(source, generated_at.date() - timedelta(days=13)),
        )

    def _totals(self, source: DashboardSource, cutoff: datetime | None) -> UsageTotals:
        source_filter = "" if source == "all" else " AND u.traffic_source = :source"
        request_time = "" if cutoff is None else " AND r.created_at >= :cutoff"
        game_time = "" if cutoff is None else " AND g.created_at >= :cutoff"
        move_time = "" if cutoff is None else " AND m.created_at >= :cutoff"
        finish_time = "" if cutoff is None else " AND g.updated_at >= :cutoff"
        puzzle_time = "" if cutoff is None else " AND p.created_at >= :cutoff"
        statement = text(
            f"""
            SELECT
              (SELECT COUNT(*) FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE 1=1{source_filter}{request_time}) AS requests,
              (SELECT COUNT(DISTINCT r.owner_key) FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE 1=1{source_filter}{request_time}) AS users,
              (SELECT COUNT(DISTINCT r.session_key)
               FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
               WHERE 1=1{source_filter}{request_time}) AS sessions,
              (SELECT COUNT(*) FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
               WHERE 1=1{source_filter}{game_time}) AS games,
              (SELECT COUNT(DISTINCT g.id) FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
               JOIN game_moves m ON m.game_id = g.id AND m.actor = 'player'
               WHERE 1=1{source_filter}{move_time}) AS engaged_games,
              (SELECT COUNT(*) FROM game_moves m JOIN games g ON g.id = m.game_id
               JOIN usage_users u ON u.owner_key = g.owner_key
               WHERE m.actor = 'player'{source_filter}{move_time}) AS player_moves,
              (SELECT COUNT(*) FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
               WHERE g.status IN ('finished', 'resigned'){source_filter}{finish_time}) AS finished_games,
              (SELECT COUNT(*) FROM puzzle_attempts p JOIN usage_users u ON u.owner_key = p.owner_key
               WHERE 1=1{source_filter}{puzzle_time}) AS puzzle_attempts
            """
        )
        parameters: dict[str, object] = {}
        if source != "all":
            parameters["source"] = source
        if cutoff is not None:
            parameters["cutoff"] = cutoff
        row = self._session.execute(statement, parameters).mappings().one()
        return UsageTotals(**{field: int(row[field]) for field in UsageTotals.__dataclass_fields__})

    def _daily(self, source: DashboardSource, start: date) -> tuple[DailyUsage, ...]:
        source_filter = "" if source == "all" else " AND u.traffic_source = :source"
        parameters: dict[str, object] = {"start": start}
        if source != "all":
            parameters["source"] = source
        days: dict[date, dict[str, int]] = {
            start + timedelta(days=offset): {
                "requests": 0,
                "users": 0,
                "sessions": 0,
                "games": 0,
                "player_moves": 0,
            }
            for offset in range(14)
        }
        requests = self._session.execute(
            text(
                f"""
                SELECT DATE(r.created_at) day, COUNT(*) requests,
                       COUNT(DISTINCT r.owner_key) users, COUNT(DISTINCT r.session_key) sessions
                FROM usage_requests r JOIN usage_users u ON u.owner_key = r.owner_key
                WHERE DATE(r.created_at) >= :start{source_filter}
                GROUP BY DATE(r.created_at)
                """
            ),
            parameters,
        ).mappings()
        for row in requests:
            day = row["day"]
            if day in days:
                days[day].update(requests=int(row["requests"]), users=int(row["users"]), sessions=int(row["sessions"]))
        games = self._session.execute(
            text(
                f"""
                SELECT DATE(g.created_at) day, COUNT(*) games
                FROM games g JOIN usage_users u ON u.owner_key = g.owner_key
                WHERE DATE(g.created_at) >= :start{source_filter}
                GROUP BY DATE(g.created_at)
                """
            ),
            parameters,
        ).mappings()
        for row in games:
            if row["day"] in days:
                days[row["day"]]["games"] = int(row["games"])
        moves = self._session.execute(
            text(
                f"""
                SELECT DATE(m.created_at) day, COUNT(*) player_moves
                FROM game_moves m JOIN games g ON g.id = m.game_id
                JOIN usage_users u ON u.owner_key = g.owner_key
                WHERE m.actor = 'player' AND DATE(m.created_at) >= :start{source_filter}
                GROUP BY DATE(m.created_at)
                """
            ),
            parameters,
        ).mappings()
        for row in moves:
            if row["day"] in days:
                days[row["day"]]["player_moves"] = int(row["player_moves"])
        return tuple(DailyUsage(day=day, **values) for day, values in days.items())


def _key(*parts: str) -> str:
    return sha256("\0".join(parts).encode()).hexdigest()
