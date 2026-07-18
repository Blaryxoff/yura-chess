from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from yura_chess import __version__
from yura_chess.settings import Settings, get_settings


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    components: dict[str, str] | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


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
    async def health_ready() -> HealthResponse:
        return HealthResponse(
            status="ready",
            service="yura-chess",
            version=__version__,
            components={"http": "ready"},
        )

    return app


app = create_app()
