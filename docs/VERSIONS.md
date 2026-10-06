# Pinned versions

Every version the project depends on: what is pinned, why, and how it was verified.
Rule 3 of the build contract: nothing is pinned from memory. If a row says "unverified", it must not be relied on.

Status meanings: **Pinned** (in a lockfile, config or image tag), **Planned** (picked but not yet used; re-check before pinning), **Unverified** (needs a check before use).

## Phase 0: tooling and data stores

| Component | Version | Where pinned | Why this version | Verified how (2026-10-06) |
|---|---|---|---|---|
| Python | 3.11 | `.python-version`, `pyproject.toml` (`>=3.11`) | Contract says 3.11+. 3.11 is the floor, so Phase 1 libraries get the widest choice of wheels | `uv python list` shows 3.11.13 locally |
| uv | >= 0.8.4 (latest is 0.12.23) | `pyproject.toml` `[tool.uv] required-version` | Manages Python versions and the lockfile (ADR-021). Floor is the version on the dev laptop; WSL2 install gets latest | GitHub releases API, astral-sh/uv |
| ruff | 0.16.10 | `pyproject.toml` (exact), `.pre-commit-config.yaml` | Latest. Must be the same in both places (tested by `test_ruff_version_matches_precommit`) | PyPI JSON API; GitHub releases, astral-sh/ruff-pre-commit |
| pytest | >= 9.1.1 (exact in `uv.lock`) | `pyproject.toml`, `uv.lock` | Latest | PyPI JSON API |
| pre-commit | >= 4.6.2 (exact in `uv.lock`) | `pyproject.toml`, `uv.lock` | Latest | PyPI JSON API |
| PyYAML | >= 6.0.3 (exact in `uv.lock`) | `pyproject.toml`, `uv.lock` | Used by tests to read compose and CI files | PyPI JSON API |
| pre-commit-hooks | v6.0.0 | `.pre-commit-config.yaml` | Latest | GitHub releases API |
| gitleaks | v8.30.1 | `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `Makefile` | Latest. Hook ids (`gitleaks`, `gitleaks-docker`, `gitleaks-system`) read from the repo's `.pre-commit-hooks.yaml` at that tag. Note: the hook runs `gitleaks git --staged`, so it only sees staged changes; CI scans full history | GitHub releases API + hook file at tag |
| actions/checkout | v7 | `.github/workflows/ci.yml` | Latest major (v7.0.1) | GitHub releases API |
| astral-sh/setup-uv | v10 | `.github/workflows/ci.yml` | Latest major (v10.2.0) | GitHub releases API |
| Qdrant | `qdrant/qdrant:v1.19.2-unprivileged` | `deploy/compose/compose.yaml` | Latest stable. The `-unprivileged` variant runs as a non-root user | Docker Hub tags API |
| Redis | `redis:8.8.3` | `deploy/compose/compose.yaml` | Latest stable. Licence note: Redis 8 is offered under AGPLv3 among other licences, which is fine for local, unmodified use | Docker Hub tags API |
| PostgreSQL | `postgres:18.6` | `deploy/compose/compose.yaml` | Latest stable (19 is still beta). **Breaking change in 18:** the data volume moved from `/var/lib/postgresql/data` to `/var/lib/postgresql` | Docker Hub tags API; official Dockerfile for 18/trixie (`VOLUME /var/lib/postgresql`, `PGDATA=/var/lib/postgresql/18/docker`) |

## Later phases: planned, re-check before pinning

| Component | Latest seen | Phase | Notes |
|---|---|---|---|
| kind | v0.33.0 (2026-08-26) | 4 | Check the node image matching the release notes |
| Helm | v4.3.0 (2026-09-09) | 4 | **Helm 4 is a major version.** Many tutorials still show Helm 3 commands and chart behaviour; read the Helm 4 docs |
| vLLM | not checked yet | 5 | Must confirm T4 (compute capability 7.5) and float16 support for the exact version (ADR-006, GPU-01) |
| LangGraph, LangChain splitters | not checked yet | 1 | APIs change quickly (ADR-007) |
| qdrant-client | not checked yet | 1 | Must be compatible with server v1.19.x |
| Embedding runtime for bge-small | not checked yet | 1 | Choice between sentence-transformers (pulls in PyTorch, large images) and an ONNX runtime such as fastembed; decided in Phase 1 with an ADR |
| OpenTelemetry SDK, Prometheus, Grafana, Langfuse SDK | not checked yet | 3 | |
| Ollama local model | not checked yet | 1 | Candidate: a small Qwen2.5 tag; confirm the exact tag exists |
