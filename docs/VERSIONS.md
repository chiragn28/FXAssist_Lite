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

## Phase 1: RAG core

Python packages: lower bounds in `services/agent/pyproject.toml`, exact versions and hashes in `uv.lock`. Every API used was checked against the installed version (signatures inspected, not assumed).

| Component | Version | Why | Verified how (2026-10-06) |
|---|---|---|---|
| langgraph | 1.2.14 | Agent control flow (ADR-007). Uses `StateGraph`, `START`, `END`, `add_conditional_edges`, `compile()`, `invoke(..., {"recursion_limit": n})`, `GraphRecursionError` | PyPI; imports and `compile` signature inspected |
| langchain-text-splitters | 1.1.3 | `RecursiveCharacterTextSplitter` only (ADR-007) | PyPI |
| qdrant-client | 1.19.1 | Matches server v1.19.2. Uses collection-level `metadata` (create and update; updates merge), `query_points`, `scroll`, and in-memory mode for tests | `create_collection`/`update_collection` signatures inspected; metadata round-trip tested in `:memory:` mode |
| fastembed | 0.8.1 | ONNX Runtime embeddings (ADR-023). **Note:** its `BAAI/bge-small-en-v1.5` is a *quantised* ONNX export (`Qdrant/bge-small-en-v1.5-onnx-Q`, MIT licence, 384 dims, truncates at 512 tokens). `passage_embed(texts, batch_size=...)` takes batch size as a keyword | `TextEmbedding.list_supported_models()`; a positional `batch_size` raised `TypeError` on first run |
| onnxruntime | 1.30.0 | Pulled in by fastembed; requires Python >= 3.11 | PyPI |
| pypdf | 6.19.0 | PDF text, one page at a time (DAT-04) | PyPI |
| trafilatura | 2.3.0 | Main-content extraction from HTML (DAT-05) | PyPI |
| httpx | 0.28.1 | Downloads and the OpenAI-compatible client; `MockTransport` in tests | PyPI |
| pydantic-settings | 2.15.0 | `FXA_*` settings from env and `.env` | PyPI |
| Ollama | `ollama/ollama:0.35.1` (wrapped as `fxassist/ollama:0.35.1`) | Latest stable (0.40.0 was still a release candidate). The image runs as root, so `deploy/compose/ollama/Dockerfile` switches to uid 1000. Default context window is **4096 tokens**, which sets the RAG context budget (RET-06) | Docker Hub tags; GitHub releases API; image config inspected (`User=` empty); `ollama ps` shows `100% GPU`, `CONTEXT 4096` |
| Local model | `qwen2.5:3b-instruct` (Q4_K_M, 2.2 GB in GPU memory) | Same model family as the GPU lab (ADR-005), quantised for a 6 GB laptop GPU | Tag listed at ollama.com/library/qwen2.5/tags; answered through `/v1/chat/completions` |
| uv_build | >=0.8.4,<0.13 | Build backend for the workspace member | uv documentation for workspace packages |

## Later phases: planned, re-check before pinning

| Component | Latest seen | Phase | Notes |
|---|---|---|---|
| kind | v0.33.0 (2026-08-26) | 4 | Check the node image matching the release notes |
| Helm | v4.3.0 (2026-09-09) | 4 | **Helm 4 is a major version.** Many tutorials still show Helm 3 commands and chart behaviour; read the Helm 4 docs |
| vLLM | not checked yet | 5 | Must confirm T4 (compute capability 7.5) and float16 support for the exact version (ADR-006, GPU-01) |
| OpenTelemetry SDK, Prometheus, Grafana, Langfuse SDK | not checked yet | 3 | |
