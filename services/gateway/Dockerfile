# FXAssist gateway image. Build from the repo root:
#   docker build -f services/gateway/Dockerfile -t fxassist/gateway .
# Multi-stage: uv and build tools stay in the build stage; the runtime stage has the virtualenv,
# the embedding model and nothing else. Runs as a non-root user (SAF-08).
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.11.17-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

# Dependencies first, in their own layer: it is rebuilt only when uv.lock changes.
COPY pyproject.toml uv.lock ./
COPY services/agent/pyproject.toml services/agent/
COPY services/gateway/pyproject.toml services/gateway/
COPY services/mock_llm/pyproject.toml services/mock_llm/
COPY services/watchdog/pyproject.toml services/watchdog/
RUN uv sync --locked --no-dev --package fxassist-gateway --no-install-workspace

COPY services/agent/src services/agent/src
COPY services/gateway/src services/gateway/src
RUN uv sync --locked --no-dev --package fxassist-gateway --no-editable

# Bake the embedding model in, so the container starts without internet access (ADR-023).
RUN /app/.venv/bin/python -c "from fastembed import TextEmbedding; \
TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/data/cache/fastembed')"

FROM python:3.11.17-slim-trixie
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build --chown=10001:10001 /app /app
# The corpus registry, for the ingestion Job on Kubernetes (`fxassist ingest`).
COPY --chown=10001:10001 data/sources.yaml /app/data/sources.yaml
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    FXA_DATA_DIR=/app/data \
    FXA_EMBED_CACHE_DIR=/app/data/cache/fastembed \
    FXA_GATEWAY_HOST=0.0.0.0
WORKDIR /app
USER 10001:10001
EXPOSE 8000
CMD ["fxassist-gateway", "serve"]
