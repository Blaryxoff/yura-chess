import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from yura_chess import __version__
from yura_chess.adapters.alice.webhook import build_router as build_alice_router
from yura_chess.adapters.yandex_images import BoardImageService
from yura_chess.engine.stockfish import StockfishPool
from yura_chess.presentation.website import (
    LANDING_PAGE_HTML,
    WEBMASTER_VERIFICATION_HTML,
    WEBMASTER_VERIFICATION_PATH,
)
from yura_chess.settings import Settings, get_settings
from yura_chess.storage.database import (
    check_connection,
    check_schema,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from yura_chess.storage.game_repository import GameRepository
from yura_chess.storage.transcript_repository import TranscriptRepository

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
        GameRepository(session).purge_request_replays(now, app.state.settings.request_replay_retention_days)
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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def landing_page() -> HTMLResponse:
        return HTMLResponse(LANDING_PAGE_HTML)

    @app.get(WEBMASTER_VERIFICATION_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def webmaster_verification() -> HTMLResponse:
        return HTMLResponse(WEBMASTER_VERIFICATION_HTML)

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
