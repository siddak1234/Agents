# Multi-stage build: builder installs deps with uv, runtime is slim.
# uv is used for reproducible, lock-based installs — faster than pip and
# with correct handling of the `[project.dependencies]` layout.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# Dependency layer — cached separately from source
COPY pyproject.toml uv.lock* /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# App layer
COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- Runtime -----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ARG APP_UID=10001
RUN groupadd --system --gid ${APP_UID} app \
    && useradd --system --uid ${APP_UID} --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY --from=builder --chown=app:app /app /app

USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"

CMD ["uvicorn", "realty_lead_gen.main:app", "--host", "0.0.0.0", "--port", "8000"]
