# Build stage: resolve the environment with uv against the lockfile.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, so code changes don't bust this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Runtime stage: plain Python, no uv, non-root.
FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --create-home agent && mkdir /data && chown agent /data
COPY --from=builder --chown=agent /app /app
USER agent
ENV PATH="/app/.venv/bin:$PATH" \
    DATABASE_PATH=/data/agent.db

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "agent.edges.whatsapp.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
