"""The screen card is an extra, never a dependency.

Two properties are load-bearing here: the renderer writes nothing to disk, and
every failure of the image path still leaves a complete voice answer.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Event
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request

import chess
import pytest
from PIL import Image, ImageDraw
from sqlalchemy.orm import Session, sessionmaker

from yura_chess.adapters.alice.models import AliceRequest, AliceResponse, BigImageCard, ResponseBody
from yura_chess.adapters.alice.webhook import _attach_card
from yura_chess.adapters.yandex_images import BoardImageCache, BoardImageService, YandexImageClient
from yura_chess.domain.game import GameStatus, PlayerColor
from yura_chess.domain.results import TurnResult, TurnStatus
from yura_chess.presentation.board_image import BOARD_PIXELS, CARD_HEIGHT, CARD_WIDTH, position_hash, render_png
from yura_chess.presentation.response_composer import compose_board_card, compose_turn
from yura_chess.settings import Settings
from yura_chess.storage.models import BoardImageCacheRow

MATE_FEN = "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "test",
        "database_url": "mysql+pymysql://user:pass@127.0.0.1:13306/unused?charset=utf8mb4",
        "identity_salt": "test-identity-salt",
        "image_upload_timeout_seconds": 1.0,
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def _result(**overrides: object) -> TurnResult:
    defaults: dict[str, object] = {
        "status": TurnStatus.OK,
        "game_id": "11111111-1111-1111-1111-111111111111",
        "revision": 3,
        "fen": chess.STARTING_FEN,
        "moves": ("e2e4", "e7e5"),
        "player_color": PlayerColor.WHITE,
        "game_status": GameStatus.ACTIVE,
        "engine_move": "e7e5",
    }
    return TurnResult(**(defaults | overrides))  # type: ignore[arg-type]


class TestRendering:
    def test_renders_a_png_of_the_expected_size(self) -> None:
        png = render_png(chess.Board(), PlayerColor.WHITE, "e2e4")

        image = Image.open(BytesIO(png))
        assert image.format == "PNG"
        assert image.size == (CARD_WIDTH, CARD_HEIGHT)

    def test_full_board_fits_inside_the_yandex_card_safe_area(self) -> None:
        horizontal_margin = (CARD_WIDTH - BOARD_PIXELS) // 2
        vertical_margin = (CARD_HEIGHT - BOARD_PIXELS) // 2
        yandex_card_ratio = 552 / 245
        cropped_height = CARD_WIDTH / yandex_card_ratio
        vertical_crop = (CARD_HEIGHT - cropped_height) / 2

        assert horizontal_margin > 0
        assert vertical_margin > vertical_crop
        assert CARD_HEIGHT - BOARD_PIXELS <= 12

    def test_uses_chess_piece_symbols_instead_of_letters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        drawn: list[str] = []
        original_text = ImageDraw.ImageDraw.text

        def record_text(self: ImageDraw.ImageDraw, xy: object, text: str, *args: object, **kwargs: object) -> None:
            drawn.append(text)
            original_text(self, xy, text, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)

        render_png(chess.Board(), PlayerColor.WHITE)

        assert set("♙♘♗♖♕♔♟♞♝♜♛♚").issubset(drawn)

    def test_writes_nothing_to_disk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        before = set(tmp_path.rglob("*"))

        render_png(chess.Board(), PlayerColor.BLACK, "e2e4")

        assert set(tmp_path.rglob("*")) == before

    def test_orientation_flips_the_board(self) -> None:
        board = chess.Board()

        assert render_png(board, PlayerColor.WHITE) != render_png(board, PlayerColor.BLACK)

    def test_last_move_highlight_changes_the_picture(self) -> None:
        board = chess.Board()

        assert render_png(board, PlayerColor.WHITE, "e2e4") != render_png(board, PlayerColor.WHITE, "d2d4")

    def test_malformed_last_move_still_renders(self) -> None:
        assert render_png(chess.Board(), PlayerColor.WHITE, "not-a-move")


class TestPositionHash:
    def test_is_stable_for_the_same_inputs(self) -> None:
        board = chess.Board()

        assert position_hash(board, PlayerColor.WHITE, "e2e4") == position_hash(board, PlayerColor.WHITE, "e2e4")

    @pytest.mark.parametrize(
        ("orientation", "last_move"),
        [(PlayerColor.BLACK, "e2e4"), (PlayerColor.WHITE, "d2d4"), (PlayerColor.WHITE, None)],
    )
    def test_covers_every_input_that_changes_pixels(self, orientation: PlayerColor, last_move: str | None) -> None:
        board = chess.Board()
        baseline = position_hash(board, PlayerColor.WHITE, "e2e4")

        assert position_hash(board, orientation, last_move) != baseline

    def test_ignores_state_that_does_not_change_pixels(self) -> None:
        """Move counters differ, placement does not: the same picture, the same key."""
        first = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        second = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 9 40")

        assert position_hash(first, PlayerColor.WHITE, None) == position_hash(second, PlayerColor.WHITE, None)


class TestCardComposition:
    def test_no_card_without_a_screen(self) -> None:
        assert compose_board_card(_result(), has_screen=False) is None

    def test_card_for_a_screen_capable_request(self) -> None:
        card = compose_board_card(_result(), has_screen=True)

        assert card is not None
        assert card.position_hash == position_hash(chess.Board(), PlayerColor.WHITE, "e7e5")
        assert Image.open(BytesIO(card.render())).format == "PNG"

    def test_speech_is_identical_with_and_without_a_screen(self) -> None:
        result = _result(fen=MATE_FEN, engine_move="a1a8")

        assert compose_turn(result).text == compose_turn(replace(result)).text

    def test_screen_interface_is_read_from_meta(self) -> None:
        base = {"session": {"message_id": 1, "session_id": "s", "skill_id": "k"}, "version": "1.0"}

        assert AliceRequest.model_validate(base | {"meta": {"interfaces": {"screen": {}}}}).has_screen
        assert not AliceRequest.model_validate(base | {"meta": {"interfaces": {}}}).has_screen


class _FakeSessionFactory:
    """Enough of `sessionmaker` for the service to be exercised without MariaDB."""

    def __init__(self, store: dict[str, BoardImageCacheRow]) -> None:
        self.store = store

    def begin(self) -> _FakeSessionContext:
        return _FakeSessionContext(self.store)


class _FakeSessionContext:
    def __init__(self, store: dict[str, BoardImageCacheRow]) -> None:
        self._store = store

    def __enter__(self) -> _FakeSession:
        return _FakeSession(self._store)

    def __exit__(self, *args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, store: dict[str, BoardImageCacheRow]) -> None:
        self._store = store

    def get(self, _model: type[BoardImageCacheRow], key: str) -> BoardImageCacheRow | None:
        return self._store.get(key)

    def merge(self, row: BoardImageCacheRow) -> BoardImageCacheRow:
        row.created_at = row.created_at or datetime.now()
        self._store[row.position_hash] = row
        return row

    def delete(self, row: BoardImageCacheRow) -> None:
        self._store.pop(row.position_hash, None)

    def query(self, _model: type[BoardImageCacheRow]) -> _FakeQuery:
        return _FakeQuery(self._store)

    def flush(self) -> None:
        return None


class _FakeQuery:
    def __init__(self, store: dict[str, BoardImageCacheRow]) -> None:
        self._store = store

    def count(self) -> int:
        return len(self._store)


@pytest.mark.anyio
class TestBoardImageService:
    async def test_uploads_once_and_reuses_the_cached_id(self) -> None:
        uploads: list[bytes] = []
        service = BoardImageService(
            _FakeSessionFactory({}),  # type: ignore[arg-type]
            _settings(),
            uploader=lambda png: uploads.append(png) or "img-1",  # type: ignore[func-returns-value]
        )

        first = await service.image_id_for("hash-1", lambda: b"png", budget_seconds=3.0)
        second = await service.image_id_for("hash-1", lambda: b"png", budget_seconds=3.0)

        assert (first, second) == ("img-1", "img-1")
        assert len(uploads) == 1

    async def test_failed_upload_returns_no_image(self) -> None:
        service = BoardImageService(_FakeSessionFactory({}), _settings(), uploader=lambda png: None)  # type: ignore[arg-type]

        assert await service.image_id_for("hash-1", lambda: b"png", budget_seconds=3.0) is None

    async def test_concurrent_cache_misses_do_not_create_duplicate_remote_images(self) -> None:
        started = Event()
        release = Event()
        uploads = 0

        def uploader(png: bytes) -> str:
            nonlocal uploads
            uploads += 1
            if uploads == 1:
                started.set()
                release.wait(timeout=2)
            return f"img-{uploads}"

        service = BoardImageService(_FakeSessionFactory({}), _settings(), uploader=uploader)  # type: ignore[arg-type]
        first = asyncio.create_task(service.image_id_for("hash-1", lambda: b"png", budget_seconds=3.0))
        assert await asyncio.to_thread(started.wait, 1)

        second = await service.image_id_for("hash-2", lambda: b"png", budget_seconds=3.0)
        release.set()

        assert await first == "img-1"
        assert second is None
        assert uploads == 1

    async def test_exhausted_budget_skips_the_upload_entirely(self) -> None:
        uploads: list[bytes] = []
        service = BoardImageService(
            _FakeSessionFactory({}),  # type: ignore[arg-type]
            _settings(),
            uploader=lambda png: uploads.append(png) or "img-1",  # type: ignore[func-returns-value]
        )

        assert await service.image_id_for("hash-1", lambda: b"png", budget_seconds=0.2) is None
        assert uploads == []

    async def test_disabled_by_settings(self) -> None:
        service = BoardImageService(
            _FakeSessionFactory({}),  # type: ignore[arg-type]
            _settings(board_image_enabled=False),
            uploader=lambda png: "img-1",
        )

        assert await service.image_id_for("hash-1", lambda: b"png", budget_seconds=3.0) is None

    async def test_idle_entry_is_reused_until_background_cleanup(self) -> None:
        stale = BoardImageCacheRow(position_hash="hash-1", image_id="old")
        stale.created_at = datetime.now() - timedelta(days=90)
        stale.last_used_at = stale.created_at
        uploads: list[bytes] = []
        service = BoardImageService(
            _FakeSessionFactory({"hash-1": stale}),  # type: ignore[arg-type]
            _settings(board_image_ttl_days=30),
            uploader=lambda png: uploads.append(png) or "img-new",  # type: ignore[func-returns-value]
        )

        assert await service.image_id_for("hash-1", lambda: b"png", budget_seconds=3.0) == "old"
        assert uploads == []


@pytest.mark.anyio
class TestAttachCard:
    """Whatever the image path does, the spoken answer leaves the adapter intact."""

    def _response(self) -> AliceResponse:
        return AliceResponse(response=ResponseBody(text="Мой ход. Е2 Е4."), version="1.0")

    async def _attach(self, has_screen: bool, images: BoardImageService | None, budget: float = 3.0) -> AliceResponse:
        return await _attach_card(self._response(), _result(), has_screen, images, budget)

    async def _service(self, uploader: object) -> BoardImageService:
        return BoardImageService(_FakeSessionFactory({}), _settings(), uploader=uploader)  # type: ignore[arg-type]

    async def test_big_image_only_for_a_screen(self) -> None:
        service = await self._service(lambda png: "img-1")

        assert (await self._attach(True, service)).response.card == BigImageCard(image_id="img-1", title="Ваш ход")
        assert (await self._attach(False, service)).response.card is None

    async def test_unavailable_image_api_leaves_the_answer_untouched(self) -> None:
        def failing(_png: bytes) -> str | None:
            raise OSError("image api down")

        response = await self._attach(True, await self._service(failing))

        assert response.response.card is None
        assert response.response.text == "Мой ход. Е2 Е4."

    async def test_slow_upload_is_abandoned_with_the_answer_intact(self) -> None:
        """A trickling upload must not eat the budget the composed reply needs."""

        def slow(_png: bytes) -> str | None:
            sleep(2.0)
            return "img-1"

        # Enough budget to start the upload, far too little to finish it.
        response = await _attach_card(self._response(), _result(), True, await self._service(slow), 1.0)

        assert response.response.card is None
        assert response.response.text == "Мой ход. Е2 Е4."

    async def test_no_image_service_configured(self) -> None:
        assert (await self._attach(True, None)).response.card is None


class TestQuotaGuardedEviction:
    def test_eviction_is_skipped_when_quota_is_unavailable(self) -> None:
        service = BoardImageService(
            _FakeSessionFactory({"hash-1": BoardImageCacheRow(position_hash="hash-1", image_id="img-1")}),  # type: ignore[arg-type]
            _settings(),
            uploader=lambda png: None,
            quota=lambda: None,
        )

        assert service.maintain_cache() == []

    def test_unconfigured_client_never_calls_the_api(self) -> None:
        client = YandexImageClient(_settings())

        assert not client.configured
        assert client.upload(b"png") is None
        assert client.quota() is None


class _HttpResponse:
    def __enter__(self) -> _HttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestYandexImageClient:
    def test_delete_uses_the_skill_resource_and_encodes_the_image_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests: list[Request] = []

        def urlopen(request: Request, timeout: float) -> _HttpResponse:
            requests.append(request)
            return _HttpResponse()

        monkeypatch.setattr("yura_chess.adapters.yandex_images.urllib.request.urlopen", urlopen)
        client = YandexImageClient(_settings(yandex_skill_id="skill-1", yandex_oauth_token="secret"))

        assert client.delete("image/with space")
        assert requests[0].get_method() == "DELETE"
        assert requests[0].full_url.endswith("/skills/skill-1/images/image%2Fwith%20space")

    @pytest.mark.parametrize("error", [HTTPError("url", 404, "missing", None, None), URLError("offline")])
    def test_delete_treats_missing_as_success_and_network_failure_as_retryable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        error: HTTPError | URLError,
    ) -> None:
        def urlopen(request: Request, timeout: float) -> _HttpResponse:
            raise error

        monkeypatch.setattr("yura_chess.adapters.yandex_images.urllib.request.urlopen", urlopen)
        client = YandexImageClient(_settings(yandex_skill_id="skill-1", yandex_oauth_token="secret"))

        assert client.delete("image-1") is isinstance(error, HTTPError)


def _seed_cached_image(
    session_factory: sessionmaker[Session],
    position_hash: str,
    image_id: str,
    last_used_at: datetime,
) -> None:
    with session_factory.begin() as session:
        row = BoardImageCacheRow(position_hash=position_hash, image_id=image_id)
        row.created_at = last_used_at
        row.last_used_at = last_used_at
        session.add(row)


def _cached_image(session_factory: sessionmaker[Session], position_hash: str) -> BoardImageCacheRow | None:
    with session_factory() as session:
        return session.get(BoardImageCacheRow, position_hash)


@pytest.mark.usefixtures("clean_image_cache")
class TestSustainableImageLifecycle:
    @pytest.mark.anyio
    async def test_remote_eviction_then_revisit_regenerates_the_position(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        position_hash = f"{1:064d}"
        _seed_cached_image(session_factory, position_hash, "img-old", datetime.now() - timedelta(days=8))
        deleted: list[str] = []
        service = BoardImageService(
            session_factory,
            _settings(board_image_ttl_days=7),
            uploader=lambda png: "img-new",
            quota=lambda: (0, 100),
            deleter=lambda image_id: deleted.append(image_id) or True,
        )

        assert service.maintain_cache() == ["img-old"]
        assert deleted == ["img-old"]
        assert _cached_image(session_factory, position_hash) is None
        assert await service.image_id_for(position_hash, lambda: b"png", budget_seconds=3.0) == "img-new"
        assert _cached_image(session_factory, position_hash).image_id == "img-new"  # type: ignore[union-attr]

    def test_failed_remote_delete_keeps_the_mapping_for_retry(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        position_hash = f"{2:064d}"
        _seed_cached_image(session_factory, position_hash, "img-old", datetime.now() - timedelta(days=8))
        service = BoardImageService(
            session_factory,
            _settings(board_image_ttl_days=7),
            quota=lambda: (90, 100),
            deleter=lambda image_id: False,
        )

        assert service.maintain_cache() == []
        assert _cached_image(session_factory, position_hash).image_id == "img-old"  # type: ignore[union-attr]

    @pytest.mark.anyio
    async def test_hard_cache_ceiling_falls_back_without_uploading(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        now = datetime.now()
        _seed_cached_image(session_factory, f"{3:064d}", "img-3", now)
        _seed_cached_image(session_factory, f"{4:064d}", "img-4", now)
        uploads: list[bytes] = []
        service = BoardImageService(
            session_factory,
            _settings(board_image_cache_limit=1, board_image_cache_burst=1),
            uploader=lambda png: uploads.append(png) or "img-new",  # type: ignore[func-returns-value]
        )

        assert await service.image_id_for(f"{5:064d}", lambda: b"png", budget_seconds=3.0) is None
        assert uploads == []


@pytest.mark.usefixtures("clean_image_cache")
class TestCacheAgainstDatabase:
    """Eviction runs against MariaDB, since its ordering is a SQL concern."""

    def test_evicts_expired_and_least_recently_used(self, session: Session) -> None:
        now = datetime.now()
        cache = BoardImageCache(session, ttl_days=30, max_entries=1)
        for index, age_days in enumerate((90, 1, 2)):
            row = BoardImageCacheRow(position_hash=f"{index:064d}", image_id=f"img-{index}")
            row.created_at = now - timedelta(days=age_days)
            row.last_used_at = now - timedelta(days=age_days)
            session.add(row)
        session.flush()

        candidates = cache.eviction_candidates(now, batch_size=10, grace_seconds=0, emergency=False)

        assert {candidate.image_id for candidate in candidates} == {"img-0", "img-2"}
        assert all(session.get(BoardImageCacheRow, candidate.position_hash) is not None for candidate in candidates)
        for candidate in candidates:
            cache.forget(candidate)
        assert cache.get(f"{1:064d}", now) == "img-1"
