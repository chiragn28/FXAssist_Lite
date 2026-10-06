# FXAssist canary watchdog image (ADR-014). Build from the repo root:
#   docker build -f services/watchdog/Dockerfile -t fxassist/watchdog .
# Small on purpose: Python and httpx only; it runs once a minute as a CronJob.
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.11.17-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY services/agent/pyproject.toml services/agent/
COPY services/gateway/pyproject.toml services/gateway/
COPY services/mock_llm/pyproject.toml services/mock_llm/
COPY services/watchdog/pyproject.toml services/watchdog/
RUN uv sync --locked --no-dev --package fxassist-watchdog --no-install-workspace
COPY services/watchdog/src services/watchdog/src
RUN uv sync --locked --no-dev --package fxassist-watchdog --no-editable

FROM python:3.11.17-slim-trixie
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build --chown=10001:10001 /app /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
USER 10001:10001
CMD ["fxassist-watchdog"]
