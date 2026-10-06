# FXAssist mock LLM image (ADR-003). Build from the repo root:
#   docker build -f services/mock_llm/Dockerfile -t fxassist/mock-llm .
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
RUN uv sync --locked --no-dev --package fxassist-mock-llm --no-install-workspace
COPY services/mock_llm/src services/mock_llm/src
RUN uv sync --locked --no-dev --package fxassist-mock-llm --no-editable

FROM python:3.11.17-slim-trixie
# The base image's pip, setuptools and wheel are not needed at runtime, and the scanner found
# HIGH CVEs in their vendored copies (CVE-2026-23949, CVE-2026-24049; Trivy, 2026-10-07).
RUN python -m pip uninstall -y -q setuptools wheel pip \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build --chown=10001:10001 /app /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FXA_MOCK_HOST=0.0.0.0 \
    FXA_MOCK_PORT=8080
USER 10001:10001
EXPOSE 8080
CMD ["fxassist-mock-llm"]
