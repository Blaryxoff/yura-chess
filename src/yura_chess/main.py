from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from yura_chess import __version__
from yura_chess.adapters.alice.webhook import build_router as build_alice_router
from yura_chess.engine.stockfish import StockfishPool
from yura_chess.settings import Settings, get_settings
from yura_chess.storage.database import (
    check_connection,
    check_schema,
    create_database_engine,
    create_session_factory,
)


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
    pool = StockfishPool(app.state.settings, process_factory=getattr(app.state, "engine_process_factory", None))
    app.state.engine_pool = pool
    await pool.start()
    try:
        yield
    finally:
        await pool.stop()
        engine.dispose()


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

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def health_live() -> HealthResponse:
        return HealthResponse(status="ok", service="yura-chess", version=__version__)

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def health_ready(response: Response) -> HealthResponse:
        database = await run_in_threadpool(_database_component, app)
        ready = database == "ready"
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ready" if ready else "degraded",
            service="yura-chess",
            version=__version__,
            components={"http": "ready", "database": database, "engine": _engine_component(app)},
        )

    app.include_router(build_alice_router())
    return app
