"""Repository integration tests against a real MariaDB 11.4."""

from __future__ import annotations

from datetime import datetime, timedelta

import chess
import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.domain.game import (
    START_FEN,
    EngineSettings,
    GameStatus,
    InvalidMoveHistoryError,
    PlayerColor,
)
from yura_chess.storage.game_repository import (
    GameNotFoundError,
    GameRepository,
    PendingTurnConflictError,
    ReplayFingerprintConflictError,
    RevisionConflictError,
)
from yura_chess.storage.models import RequestReplayRow

OWNER = "a" * 64
OTHER_OWNER = "b" * 64
FINGERPRINT = "f" * 64
OTHER_FINGERPRINT = "e" * 64


def _new_game(repository: GameRepository, session: Session, owner: str = OWNER) -> str:
    game = repository.create_game(owner_key=owner, player_color=PlayerColor.WHITE)
    session.commit()
    return game.id


def test_database_is_mariadb_11_4(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        version = str(connection.execute(text("SELECT VERSION()")).scalar_one())

    assert "MariaDB" in version, f"expected MariaDB, got {version}"
    assert version.startswith("11.4"), f"expected MariaDB 11.4, got {version}"


def test_create_and_load_game(repository: GameRepository, session: Session) -> None:
    created = repository.create_game(
        owner_key=OWNER,
        player_color=PlayerColor.BLACK,
        engine=EngineSettings(skill_level=8, move_time_ms=1500),
    )
    session.commit()

    loaded = repository.load(created.id, OWNER)

    assert loaded.id == created.id
    assert loaded.owner_key == OWNER
    assert loaded.status is GameStatus.ACTIVE
    assert loaded.player_color is PlayerColor.BLACK
    assert loaded.initial_fen == START_FEN
    assert loaded.moves == ()
    assert loaded.revision == 1
    assert loaded.engine == EngineSettings(skill_level=8, move_time_ms=1500)
    assert loaded.pending_engine_turn is None


def test_board_is_rebuilt_by_replaying_the_whole_history(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    repository.append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4", "e7e5", "g1f3"))
    session.commit()

    state = repository.load(game_id, OWNER)
    board = state.board()

    expected = chess.Board(START_FEN)
    for uci in ("e2e4", "e7e5", "g1f3"):
        expected.push_uci(uci)

    assert state.moves == ("e2e4", "e7e5", "g1f3")
    assert board.fen() == expected.fen()
    assert board.piece_at(chess.F3) == chess.Piece(chess.KNIGHT, chess.WHITE)
    assert board.turn == chess.BLACK
    assert state.is_player_to_move is False


def test_moves_keep_their_order_across_appends(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    revision = 1
    for uci in ("d2d4", "d7d5", "c2c4", "e7e6"):
        state = repository.append_moves(game_id, OWNER, expected_revision=revision, moves=(uci,))
        session.commit()
        revision = state.revision

    assert repository.load(game_id, OWNER).moves == ("d2d4", "d7d5", "c2c4", "e7e6")


def test_corrupted_history_is_rejected_on_replay(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    repository.append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4", "e2e4"))
    session.commit()

    with pytest.raises(InvalidMoveHistoryError):
        repository.load(game_id, OWNER).board()


def test_another_owner_cannot_load_the_game(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)

    assert repository.find(game_id, OTHER_OWNER) is None
    with pytest.raises(GameNotFoundError):
        repository.load(game_id, OTHER_OWNER)


def test_another_owner_cannot_modify_the_game(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)

    with pytest.raises(GameNotFoundError):
        repository.append_moves(game_id, OTHER_OWNER, expected_revision=1, moves=("e2e4",))
    session.rollback()

    assert repository.load(game_id, OWNER).moves == ()


def test_unknown_game_id_is_indistinguishable_from_a_foreign_one(repository: GameRepository) -> None:
    assert repository.find("00000000-0000-0000-0000-000000000000", OWNER) is None


def test_games_of_different_owners_are_independent(repository: GameRepository, session: Session) -> None:
    mine = _new_game(repository, session, OWNER)
    theirs = _new_game(repository, session, OTHER_OWNER)

    repository.append_moves(mine, OWNER, expected_revision=1, moves=("e2e4",))
    session.commit()

    assert repository.load(mine, OWNER).moves == ("e2e4",)
    assert repository.load(theirs, OTHER_OWNER).moves == ()


def test_revision_increases_on_every_write(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)

    first = repository.append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4",))
    session.commit()
    second = repository.append_moves(game_id, OWNER, expected_revision=first.revision, moves=("e7e5",))
    session.commit()

    assert (first.revision, second.revision) == (2, 3)


def test_stale_revision_is_rejected(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    repository.append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4",))
    session.commit()

    with pytest.raises(RevisionConflictError):
        repository.append_moves(game_id, OWNER, expected_revision=1, moves=("d2d4",))
    session.rollback()

    assert repository.load(game_id, OWNER).moves == ("e2e4",)


def test_concurrent_writers_lose_the_second_write(
    repository: GameRepository,
    session: Session,
    session_factory: sessionmaker[Session],
) -> None:
    game_id = _new_game(repository, session)

    with session_factory() as concurrent_session:
        concurrent = GameRepository(concurrent_session)
        repository.append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4",))
        session.commit()

        with pytest.raises(RevisionConflictError):
            concurrent.append_moves(game_id, OWNER, expected_revision=1, moves=("d2d4",))
        concurrent_session.rollback()

    assert repository.load(game_id, OWNER).moves == ("e2e4",)


def test_a_writer_that_already_loaded_the_game_still_sees_a_newer_revision(
    repository: GameRepository,
    session: Session,
    session_factory: sessionmaker[Session],
) -> None:
    game_id = _new_game(repository, session)
    session.commit()

    # Loading first is what the engine-turn path does before it writes. The
    # locking read inside append_moves must report the revision as it is now, not
    # the one this session already read, or both writers claim the same ply.
    loaded = repository.load(game_id, OWNER)

    with session_factory() as concurrent_session:
        GameRepository(concurrent_session).append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4",))
        concurrent_session.commit()

    with pytest.raises(RevisionConflictError):
        repository.append_moves(game_id, OWNER, expected_revision=loaded.revision, moves=("d2d4",))
    session.rollback()

    assert repository.load(game_id, OWNER).moves == ("e2e4",)


def test_pending_engine_turn_round_trip(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    after_move = repository.append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4",))
    pending = repository.set_pending_engine_turn(
        game_id,
        OWNER,
        expected_revision=after_move.revision,
        token="7f1c0d1e-0000-4000-8000-000000000001",
        player_move_uci="e2e4",
    )
    session.commit()

    reloaded = repository.load(game_id, OWNER)
    assert reloaded.pending_engine_turn is not None
    assert reloaded.pending_engine_turn.player_move_uci == "e2e4"

    repository.clear_pending_engine_turn(game_id, OWNER, expected_revision=pending.revision)
    session.commit()

    assert repository.load(game_id, OWNER).pending_engine_turn is None


def test_second_pending_engine_turn_is_rejected(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    pending = repository.set_pending_engine_turn(
        game_id, OWNER, expected_revision=1, token="token-1", player_move_uci="e2e4"
    )
    session.commit()

    with pytest.raises(PendingTurnConflictError):
        repository.set_pending_engine_turn(
            game_id, OWNER, expected_revision=pending.revision, token="token-2", player_move_uci="d2d4"
        )
    session.rollback()


def test_status_transition_is_persisted(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)

    repository.append_moves(game_id, OWNER, expected_revision=1, moves=(), status=GameStatus.RESIGNED)
    session.commit()

    assert repository.load(game_id, OWNER).status is GameStatus.RESIGNED


def test_exact_replay_returns_the_stored_record(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    first, created = repository.record_request("skill", "session", "message-1", FINGERPRINT, OWNER, game_id)
    repository.store_response(first, '{"response": "ok"}')
    session.commit()

    second, replayed = repository.record_request("skill", "session", "message-1", FINGERPRINT, OWNER, game_id)

    assert (created, replayed) == (True, False)
    assert second.id == first.id
    assert second.response_payload == '{"response": "ok"}'


def test_replay_key_reused_with_another_fingerprint_is_rejected(repository: GameRepository, session: Session) -> None:
    game_id = _new_game(repository, session)
    repository.record_request("skill", "session", "message-1", FINGERPRINT, OWNER, game_id)
    session.commit()

    with pytest.raises(ReplayFingerprintConflictError):
        repository.record_request("skill", "session", "message-1", OTHER_FINGERPRINT, OWNER, game_id)
    session.rollback()

    assert repository.load(game_id, OWNER).moves == ()


def test_replay_key_reused_by_another_owner_is_rejected(repository: GameRepository, session: Session) -> None:
    repository.record_request("skill", "session", "message-1", FINGERPRINT, OWNER)
    session.commit()

    with pytest.raises(ReplayFingerprintConflictError):
        repository.record_request("skill", "session", "message-1", FINGERPRINT, OTHER_OWNER)
    session.rollback()


def test_same_message_id_in_another_session_is_a_distinct_request(repository: GameRepository, session: Session) -> None:
    first, _ = repository.record_request("skill", "session-a", "message-1", FINGERPRINT, OWNER)
    second, _ = repository.record_request("skill", "session-b", "message-1", FINGERPRINT, OWNER)
    session.commit()

    assert first.id != second.id


def test_concurrent_delivery_of_the_same_request_yields_one_record(
    repository: GameRepository,
    session: Session,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as concurrent_session:
        concurrent = GameRepository(concurrent_session)
        first, created = repository.record_request("skill", "session", "message-1", FINGERPRINT, OWNER)
        session.commit()

        second, replayed = concurrent.record_request("skill", "session", "message-1", FINGERPRINT, OWNER)
        concurrent_session.commit()

    assert (created, replayed) == (True, False)
    assert first.id == second.id


def test_replay_retention_removes_only_expired_rows(repository: GameRepository, session: Session) -> None:
    now = datetime(2026, 7, 19, 12, 0, 0)
    stale, _ = repository.record_request("skill", "old", "1", FINGERPRINT, OWNER)
    fresh, _ = repository.record_request("skill", "new", "1", OTHER_FINGERPRINT, OWNER)
    stale.created_at = now - timedelta(days=8)
    fresh.created_at = now - timedelta(days=1)
    session.flush()

    removed = repository.purge_request_replays(now, retention_days=7)
    session.commit()

    assert removed == 1
    assert session.scalars(select(RequestReplayRow.id)).all() == [fresh.id]


def test_replay_recovery_sees_a_row_committed_after_the_snapshot_opened(
    repository: GameRepository,
    session: Session,
    session_factory: sessionmaker[Session],
) -> None:
    """The losing side of an insert race must return the winner's record."""
    # Open this session's read snapshot before the rival row exists.
    assert repository.find("00000000-0000-0000-0000-000000000000", OWNER) is None

    with session_factory() as rival_session:
        winner, _ = GameRepository(rival_session).record_request("skill", "session", "message-1", FINGERPRINT, OWNER)
        rival_session.commit()
        winner_id = winner.id

    loser, replayed = repository.record_request("skill", "session", "message-1", FINGERPRINT, OWNER)
    session.commit()

    assert replayed is False
    assert loser.id == winner_id


def test_row_lock_blocks_the_second_writer_until_the_first_commits(
    repository: GameRepository,
    session: Session,
    session_factory: sessionmaker[Session],
) -> None:
    import threading

    game_id = _new_game(repository, session)
    outcome: list[object] = []
    started = threading.Event()

    def second_writer() -> None:
        with session_factory() as other_session:
            other = GameRepository(other_session)
            started.set()
            try:
                other.append_moves(game_id, OWNER, expected_revision=1, moves=("d2d4",))
                other_session.commit()
                outcome.append("committed")
            except Exception as error:  # noqa: BLE001 - the test asserts on the type
                other_session.rollback()
                outcome.append(error)

    # Take the row lock first and hold it while the second writer contends for it.
    repository.append_moves(game_id, OWNER, expected_revision=1, moves=("e2e4",))
    thread = threading.Thread(target=second_writer)
    thread.start()
    started.wait(timeout=5)
    session.commit()
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], RevisionConflictError), outcome[0]
    assert repository.load(game_id, OWNER).moves == ("e2e4",)
