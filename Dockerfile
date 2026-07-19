# Build the virtualenv in a throwaway stage so the runtime image carries no
# compiler, no uv cache and no build metadata.
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile alone, so editing sources does not
# invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

# Stockfish comes from the distribution: the image must never ship a vendored binary.
RUN apt-get update \
    && apt-get install --no-install-recommends -y stockfish \
    && rm -rf /var/lib/apt/lists/*

# A fixed non-root uid keeps volume ownership predictable across hosts.
RUN groupadd --gid 10001 yura && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin yura

WORKDIR /app

COPY --from=builder --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root src ./src
COPY --chown=root:root migrations ./migrations
COPY --chown=root:root alembic.ini ./

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    YURA_CHESS_STOCKFISH_PATH=/usr/games/stockfish

USER yura
EXPOSE 8000

# The container answers only on its own loopback-published port; nginx on the
# host terminates TLS. See deploy/INFRASTRUCTURE.md.
# Liveness, not readiness: a failing check restarts the container, and restarting
# does not bring back a missing Stockfish binary or a down database. Readiness is
# the deploy gate instead (deploy/deploy.sh).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"

CMD ["uvicorn", "--factory", "yura_chess.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
