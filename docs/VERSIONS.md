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

## Phase 2: API service

Lower bounds in each service's `pyproject.toml`, exact versions and hashes in `uv.lock`. APIs used were checked in the installed versions (signatures and source inspected, not assumed).

| Component | Version | Why | Verified how (2026-10-06) |
|---|---|---|---|
| FastAPI | 0.142.2 | Gateway and mock LLM | PyPI JSON API |
| Starlette | 1.7.0 (via FastAPI) | `StreamingResponse` checks the ASGI spec version: from 2.4 it relies on send errors instead of listening for `http.disconnect`. The gateway uses its own SSE response that always listens, so disconnects are caught either way (LLM-05) | Source of `StreamingResponse.__call__` read in the installed version |
| uvicorn | 0.54.0 | ASGI server. Uses `Config(timeout_graceful_shutdown=...)` and overrides `Server.handle_exit(sig, frame)` to set the draining flag (API-08). Signal handlers are only installed on the main thread | Signatures and `capture_signals` source inspected |
| redis (redis-py) | 8.1.0 | Async client and `register_script`. **Note:** since redis-py 6 the client retries 3 times with backoff by default; the gateway passes `retry=Retry(NoBackoff(), 0)` so its own fail-open policy decides (CAC-01) | `Redis.__init__` source inspected |
| psycopg / psycopg-binary | 3.3.6 | PostgreSQL driver (ADR-011) | PyPI |
| psycopg-pool | 3.3.3 | `AsyncConnectionPool(open=False, timeout=..., reconnect_timeout=...)`, `open(wait=False)`: the pool connects in the background so the gateway starts without PostgreSQL (DEP-04) | Signature inspected |
| opentelemetry-api / -sdk | 1.45.1 | Metric instruments now; exporters in Phase 3. Tests read counters with `InMemoryMetricReader` | PyPI (released the same day; re-check before Phase 3) |
| fakeredis (dev) | 2.39.0, with `lupa` 2.8 | Redis for offline tests, including the Lua token bucket and `TIME` | PyPI; Lua script runs in tests |
| uv (in images) | `ghcr.io/astral-sh/uv:0.12.23` | Copied into the build stage only | GitHub releases API; image pulled |
| Python base image | `python:3.11.17-slim-trixie` | Matches `.python-version` (3.11), Debian 13 slim | Docker Hub tags API; images built |
| Ollama streaming format | as served by 0.35.1 | Captured for the mock's `ollama` flavour: first chunk has `role` and content, an empty-delta chunk carries `finish_reason`, then a `choices: []` usage chunk (with `stream_options.include_usage`), then `[DONE]`. Unknown model: HTTP 404 with an OpenAI-style error body | `curl` against the running server |

Measured sizes (2026-10-06): gateway image about 590 MB of content (virtualenv 402 MB, of which ONNX Runtime 68 MB, NumPy 74 MB, ingestion-only libraries such as lxml and Babel about 50 MB; embedding model 65 MB; Python base). Gateway resident memory 347 MiB after the demo. Mock LLM 44 MiB.

## Phase 3: observability

| Component | Version | Why | Verified how (2026-10-06/07) |
|---|---|---|---|
| Prometheus | `prom/prometheus:v3.15.0` | Latest stable (v3.15.0, 2026-09-25). Runs as `nobody` (65534) by default. Rules validated with the image's `promtool check rules` | GitHub releases API; Docker Hub tags; `promtool` run |
| Grafana | `grafana/grafana:13.2.3` | Latest stable (2026-09-29). Runs as uid 472. Dashboards provisioned from files; classic dashboard JSON (`schemaVersion` 41) loads in 13.2 | GitHub releases API; Docker Hub tags; dashboard read back through `/api/dashboards/uid/...` |
| opentelemetry-exporter-prometheus | 0.66b1 (beta, as all OTel Prometheus exporter releases are) | `PrometheusMetricReader()` + `prometheus_client.start_http_server(port, addr)`. Names: counters get `_total`, histograms with unit `s` get `_seconds`; curly-brace units like `{request}` are dropped | Signature inspected; `/metrics` output read |
| prometheus-client | 0.26.0 | Pulled in by the exporter; serves `/metrics` on its own port | PyPI |
| opentelemetry-exporter-otlp-proto-http | 1.45.1 | `OTLPSpanExporter(endpoint=..., headers=..., timeout=...)`; `BatchSpanProcessor(max_queue_size, max_export_batch_size <= max_queue_size, export_timeout_millis)` | Signatures inspected; DEP-03 tests against refused, 429 and hanging endpoints |
| FastAPI built-in telemetry | 0.142.2 | **Observed, not documented by me before:** with an OTel SDK installed, FastAPI emits its own server span (`POST /v1/ask`), `fastapi.endpoint`/`fastapi.dependencies` spans and `http.server.*` metrics labelled by route template. The gateway's own span is named `fxassist.ask` to avoid a duplicate name | Spans listed from an in-memory exporter in a test run |
| Langfuse Cloud (optional) | Hobby tier | OTLP/HTTP only (no gRPC) at `https://cloud.langfuse.com/api/public/otel/v1/traces` (EU) or `us.cloud.langfuse.com` (US); `Authorization: Basic base64(public:secret)`; header `x-langfuse-ingestion-version: 4`. Maps `gen_ai.request.model`, `gen_ai.usage.*`, `gen_ai.prompt`/`gen_ai.completion`, `input.value`/`output.value`. Free tier: 50k units/month, 30 days data access, 2 users, no credit card. Not verified: what happens past 50k units on the free tier (the pricing page does not say), so tracing stays optional and failure-tolerant (DEP-03) | langfuse.com/docs/opentelemetry/get-started, langfuse.com/integrations/native/opentelemetry, langfuse.com/pricing, read 2026-10-06. **No account was created; export to Langfuse itself is untested** |

## Phase 4: Kubernetes

Binaries installed into `~/.local/bin` (user-level, no sudo) from the official release URLs, each checked against its published SHA-256.

| Component | Version | Why | Verified how (2026-10-07) |
|---|---|---|---|
| kind | v0.33.0 | Latest release (2026-08-26) | GitHub releases API; `sha256sum -c` of `kind-linux-amd64.sha256sum` |
| kind node image | `kindest/node:v1.37.0@sha256:a1ed56cf...580ae5` | The default image named in the kind v0.33.0 release notes, pinned by digest in `deploy/kind/cluster.yaml` | Release notes |
| kubectl | v1.37.1 | The installed v1.32.2 was outside the supported skew (±1 minor) for a v1.37 cluster; v1.37.1 is `stable-1.37.txt` | dl.k8s.io checksum |
| Helm | v4.3.0 | Latest (2026-09-09). **Helm 4 differences used here:** `--wait` alone means the kstatus "watcher" strategy (default without the flag: `hookOnly`); `--wait-for-jobs`; `--atomic` is now `--rollback-on-failure`; server-side apply by default for new releases; post-renderers are plugins. Chart `apiVersion: v2` works unchanged (v3 charts are experimental) | helm.sh/docs/overview; `helm upgrade --help`; `sha256sum -c` |
| metrics-server | v0.9.0 | For the gateway HPA; patched with `--kubelet-insecure-tls` because kind's kubelets use self-signed certificates | GitHub releases API; HPA reported `cpu: 1%/70%` |
| nginx (proxy check only) | `nginxinc/nginx-unprivileged:1.30.5-alpine` | Current stable line, non-root image; used only by `scripts/kind_proxy_check.sh` | Docker Hub tags |
| Our images | `fxassist/{gateway,mock-llm,watchdog}:0.4.0` | Built by `make images`. Compressed sizes: 234 MB, 51 MB, 47 MB; budgets 270/60/55 MB in `scripts/image_budget.py` | gzip size of `docker save` (store-independent). `docker image inspect .Size` is compressed with the containerd store but uncompressed on GitHub's runners (589 MB for the same gateway image), which broke the first CI run |

**kind with Docker's containerd image store:** `kind load docker-image` failed with `ctr: content digest ...: not found`, because it exports every platform of an image index. `make kind-load` exports one platform (`docker save --platform linux/amd64`) and runs `kind load image-archive`.

## Phase 5: CI/CD and the GPU lab

| Component | Version | Why | Verified how (2026-10-07) |
|---|---|---|---|
| vLLM (Kaggle notebook only) | 0.31.0 (2026-10-05) | Latest. Compute capability >= 7.5 (T4 listed). Every CLI flag the lab uses was checked in `vllm serve --help=all` of the installed wheel. On SM 7.5: FlashAttention requires 8.0, FlashInfer is deliberately disabled ("currently broken on SM75"), TRITON_ATTN accepts any: the expected backend is TRITON_ATTN (the smoke test records it). `ignore_eos` exists in the chat completions request model. Startup log formats for memory (`Model loading took`, `Available KV cache memory`, `KV cache size`) read from the source | docs.vllm.ai GPU install page and quantization table; PyPI; the 0.31.0 wheel installed in a throwaway virtualenv (bundled PyTorch, CUDA 12.9 build) |
| Qwen/Qwen2.5-3B-Instruct | revision `aa8e7253799`, 6.17 GB | ADR-005. `config.json` dtype bfloat16, so `--dtype float16` on a T4 (GPU-02). 36 layers, 2 KV heads, head size 128: 36 KiB of KV cache per token in FP16. **Qwen Research License** (non-commercial) | huggingface.co API, config.json, LICENSE |
| Qwen/Qwen2.5-3B-Instruct-AWQ | revision `3559b226e8c`, 2.69 GB | 4-bit AWQ GEMM, group size 128; AWQ supported on Turing per vLLM docs. Qwen Research License | huggingface.co API |
| peft (Kaggle fine-tuning only) | 0.21.2 (2026-10-01) | LoRA adapters, `merge_and_unload` (ADR-026). Installed into the vLLM virtualenv with vLLM pinned, so torch and transformers stay vLLM's. Tested locally 2026-10-07 with transformers 5.19.0 and torch 2.11.0+cu128 (the laptop driver supports CUDA 12.8; torch 2.14.1 from PyPI needs a newer driver) | PyPI JSON API, 2026-10-07 |
| accelerate (Kaggle fine-tuning only) | 1.15.0 (2026-09-09) | Required by peft | PyPI JSON API, 2026-10-07 |
| Qwen/Qwen2.5-7B-Instruct-AWQ (stretch) | revision `b25037543e9`, 5.57 GB | Apache-2.0 | huggingface.co API |
| Kaggle free tier | | **Unverified** (official pages render client-side); see the table in `docs/KAGGLE_PLAYBOOK.md` | secondary sources only |
| Trivy | v0.75.0 | Vulnerability scan in CI, report only. Release binary with checksum, not the GitHub Action | GitHub releases API; checksum file; scan run locally: 2 HIGH (setuptools' vendored jaraco.context CVE-2026-23949, wheel CVE-2026-24049) in all images, fixed by removing pip/setuptools/wheel from the runtime stage: 0 after |
| actionlint | v1.7.12 (docker image) | Workflow lint, run locally | ran clean |
| GitHub Actions used | `actions/checkout@v7`, `astral-sh/setup-uv@v10` | Only these two; Helm, kind, kubectl, gitleaks and Trivy are downloaded and checksum-verified | GitHub releases API |

## GPU lab tooling

| Component | Version | Where | Why | Verified how |
|---|---|---|---|---|
| Kaggle CLI | 2.2.4 (run with `uvx`, not a project dependency) | `scripts/kaggle_lab.sh` | Starts and downloads GPU-lab runs without the browser. 2.x signs in with `kaggle auth login` or an access token and cannot report the username, so it comes from `FXA_KAGGLE_USERNAME` | PyPI JSON API and `kaggle auth --help` on 2026-10-07; kernel metadata fields from the CLI's `docs/kernels_metadata.md` |
