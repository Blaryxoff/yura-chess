from __future__ import annotations

from datetime import datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text


def test_legacy_puzzle_plays_are_backfilled_once(database_engine: Engine) -> None:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    moment = datetime(2026, 9, 16, 12, 5, 10)
    owner = "a" * 64
    attempts = [
        {"puzzle_id": "solved", "status": "solved", "node": 0, "mistakes": 0, "hints": 0, "run_key": None},
        {"puzzle_id": "wrong", "status": "active", "node": 0, "mistakes": 1, "hints": 0, "run_key": None},
        {"puzzle_id": "partial", "status": "abandoned", "node": 2, "mistakes": 0, "hints": 0, "run_key": None},
        {"puzzle_id": "opened", "status": "active", "node": 0, "mistakes": 0, "hints": 0, "run_key": None},
        {"puzzle_id": "hinted", "status": "active", "node": 0, "mistakes": 0, "hints": 1, "run_key": None},
        {"puzzle_id": "revealed", "status": "failed", "node": 0, "mistakes": 0, "hints": 0, "run_key": None},
        {"puzzle_id": "new", "status": "solved", "node": 0, "mistakes": 0, "hints": 0, "run_key": "1" * 36},
    ]
    command.downgrade(config, "0020")
    try:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO usage_users (owner_key, traffic_source, first_seen_at, last_seen_at) "
                    "VALUES (:owner, 'real', :moment, :moment)"
                ),
                {"owner": owner, "moment": moment},
            )
            connection.execute(
                text(
                    "INSERT INTO puzzle_attempts "
                    "(owner_key, puzzle_id, status, node, mistakes, hints, revision, run_key, created_at, updated_at) "
                    "VALUES (:owner, :puzzle_id, :status, :node, :mistakes, :hints, 1, :run_key, :moment, :moment)"
                ),
                [{**attempt, "owner": owner, "moment": moment} for attempt in attempts],
            )
            connection.execute(
                text(
                    "INSERT INTO usage_puzzle_plays (run_key, owner_key, created_at) VALUES (:run_key, :owner, :moment)"
                ),
                {"run_key": "1" * 36, "owner": owner, "moment": moment},
            )

        command.upgrade(config, "head")
        with database_engine.connect() as connection:
            played = dict(
                connection.execute(
                    text(
                        "SELECT a.puzzle_id, p.created_at FROM puzzle_attempts a "
                        "JOIN usage_puzzle_plays p ON p.run_key = a.run_key"
                    )
                ).all()
            )
            unplayed = (
                connection.execute(
                    text("SELECT puzzle_id FROM puzzle_attempts WHERE run_key IS NULL ORDER BY puzzle_id")
                )
                .scalars()
                .all()
            )
        assert played == {puzzle_id: moment for puzzle_id in ("solved", "wrong", "partial", "new")}
        assert unplayed == ["hinted", "opened", "revealed"]

        command.downgrade(config, "0020")
        command.upgrade(config, "head")
        with database_engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM usage_puzzle_plays")).scalar_one()
        assert count == 4
    finally:
        command.upgrade(config, "head")
