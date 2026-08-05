import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from yura_chess import __version__
from yura_chess.adapters.alice.webhook import build_router as build_alice_router
from yura_chess.adapters.yandex_images import BoardImageService
from yura_chess.engine.stockfish import StockfishPool
from yura_chess.presentation.dashboard import ChartMetric, render_dashboard
from yura_chess.presentation.social_card import SOCIAL_CARD_PATH, SOCIAL_CARD_PNG
from yura_chess.presentation.website import (
    ACCESSIBILITY_PAGE_HTML,
    ACCESSIBILITY_PATH,
    BLINDFOLD_PAGE_HTML,
    BLINDFOLD_PATH,
    COACH_PAGE_HTML,
    COACH_PATH,
    COMMANDS_PAGE_HTML,
    COMMANDS_PATH,
    FAVICON_PATH,
    FAVICON_SVG,
    HOW_TO_PLAY_PAGE_HTML,
    HOW_TO_PLAY_PATH,
    INDEXNOW_KEY,
    INDEXNOW_KEY_PATH,
    PUZZLES_PAGE_HTML,
    PUZZLES_PATH,
    ROBOTS_PATH,
    ROBOTS_TEXT,
    SITEMAP_PATH,
    SITEMAP_XML,
    WEBMASTER_VERIFICATION_HTML,
    WEBMASTER_VERIFICATION_PATH,
    render_landing_page,
)
from yura_chess.settings import Settings, get_settings
from yura_chess.storage.analysis_repository import AnalysisRepository
from yura_chess.storage.database import (
    check_connection,
    check_schema,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.review_repository import ReviewRepository
from yura_chess.storage.transcript_repository import TranscriptRepository
from yura_chess.storage.usage_repository import UsageRepository

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    components: dict[str, str] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_database_engine(app.state.settings)
    app.state.database_engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.board_images = BoardImageService(app.state.session_factory, app.state.settings)
    pool = StockfishPool(app.state.settings, process_factory=getattr(app.state, "engine_process_factory", None))
    app.state.engine_pool = pool
    await pool.start()
    maintenance = asyncio.create_task(_maintenance_loop(app))
    try:
        yield
    finally:
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)
        await pool.stop()
        engine.dispose()


async def _maintenance_loop(app: FastAPI) -> None:
    while True:
        try:
            await run_in_threadpool(_purge_retained_data, app)
        except Exception:  # noqa: BLE001 - maintenance failure must not stop games
            logger.exception("maintenance failed")
        await asyncio.sleep(app.state.settings.maintenance_interval_seconds)


def _purge_retained_data(app: FastAPI) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_scope(app.state.session_factory) as session:
        TranscriptRepository(session).purge_expired(now, app.state.settings.asr_transcript_retention_days)
        games = GameRepository(session)
        games.purge_request_replays(now, app.state.settings.request_replay_retention_days)
        games.purge_test_games(now, app.state.settings.test_game_retention_days)
        AnalysisRepository(session).purge_expired(now, app.state.settings.analysis_checkpoint_retention_days)
        ReviewRepository(session).purge_expired(now, app.state.settings.review_state_retention_days)
    app.state.board_images.maintain_cache()


def _database_component(app: FastAPI) -> str:
    """Readiness reports a broken database; it never fails the request itself."""
    engine = getattr(app.state, "database_engine", None)
    if engine is None:
        return "unavailable: engine not initialised"
    try:
        check_connection(engine)
        check_schema(engine)
    except Exception as error:  # noqa: BLE001 - any failure means "not ready"
        return f"unavailable: {type(error).__name__}"
    return "ready"


def _engine_component(app: FastAPI) -> str:
    """Report worker readiness by count only; never start a search from a health probe."""
    pool: StockfishPool | None = getattr(app.state, "engine_pool", None)
    if pool is None:
        return "unavailable: pool not initialised"
    ready = pool.ready_workers
    return f"{'ready' if ready else 'degraded'}: {ready}/{pool.size} workers"


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="Шахматы с Юрой",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings or get_settings()

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
    async def landing_page(
        period: Literal["month", "year", "all"] = "month",
        metric: ChartMetric = "requests",
    ) -> HTMLResponse:
        def load() -> str:
            with session_scope(app.state.session_factory) as session:
                snapshot = UsageRepository(session).dashboard("real", period=period)
                return render_landing_page(render_dashboard(snapshot, metric))

        return HTMLResponse(
            await run_in_threadpool(load),
            headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=300"},
        )

    def _static_page(path: str, html: str) -> None:
        """Serve one crawlable page.

        The content only changes on release, but a long max-age with no validator
        strands readers on the previous version for the whole window. A content
        ETag keeps the revalidation cheap and makes a release visible at once.
        """
        etag = f'"{sha256(html.encode()).hexdigest()[:16]}"'
        headers = {"Cache-Control": "public, max-age=300, stale-while-revalidate=3600", "ETag": etag}

        @app.api_route(path, methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
        async def page(request: Request) -> Response:
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            return HTMLResponse(html, headers=headers)

    _static_page(HOW_TO_PLAY_PATH, HOW_TO_PLAY_PAGE_HTML)
    _static_page(COMMANDS_PATH, COMMANDS_PAGE_HTML)
    _static_page(COACH_PATH, COACH_PAGE_HTML)
    _static_page(PUZZLES_PATH, PUZZLES_PAGE_HTML)
    _static_page(ACCESSIBILITY_PATH, ACCESSIBILITY_PAGE_HTML)
    _static_page(BLINDFOLD_PATH, BLINDFOLD_PAGE_HTML)

    @app.get(WEBMASTER_VERIFICATION_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def webmaster_verification() -> HTMLResponse:
        return HTMLResponse(WEBMASTER_VERIFICATION_HTML)

    @app.api_route(SOCIAL_CARD_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    async def social_card() -> Response:
        return Response(SOCIAL_CARD_PNG, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.api_route(INDEXNOW_KEY_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    async def indexnow_key() -> Response:
        return Response(INDEXNOW_KEY, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400"})

    @app.api_route(ROBOTS_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    async def robots() -> Response:
        return Response(ROBOTS_TEXT, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400"})

    @app.api_route(SITEMAP_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    async def sitemap() -> Response:
        return Response(SITEMAP_XML, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})

    @app.api_route(FAVICON_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    async def favicon() -> Response:
        return Response(FAVICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def health_live() -> HealthResponse:
        return HealthResponse(status="ok", service="yura-chess", version=__version__)

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def health_ready(response: Response) -> HealthResponse:
        database = await run_in_threadpool(_database_component, app)
        engine = _engine_component(app)
        # With no ready worker every turn answers "still thinking", so such an
        # instance has to leave rotation instead of accepting traffic.
        ready = database == "ready" and engine.startswith("ready")
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ready" if ready else "degraded",
            service="yura-chess",
            version=__version__,
            components={"http": "ready", "database": database, "engine": engine},
        )

    app.include_router(build_alice_router())
    return app
