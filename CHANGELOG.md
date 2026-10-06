# Changelog

Notable changes per phase. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Phase 4: Containers, Helm, kind, reliability (2026-10-07)

### Added
- Helm chart `deploy/helm/fxassist` (Helm 4, chart apiVersion v2): gateway (2 replicas, startup/readiness/liveness probes, preStop drain, PDB, HPA, read-only root filesystem, metrics on a ClusterIP-only Service), mock LLM, Qdrant and PostgreSQL StatefulSets, Redis, a per-revision ingestion Job, and the watchdog CronJob with a Role limited to one Deployment. Base values plus small kind and CI overrides.
- `services/watchdog` (`fxassist-watchdog`): canary prompt with content and latency checks, restart policy as a pure function (3 consecutive failures, 10 min cooldown, 2 restarts per hour), state in a Deployment annotation, three REST calls instead of a Kubernetes client library.
- kind cluster config pinned to the node image by digest; Make targets `images`, `image-budget`, `kind-up`, `kind-load`, `kind-deploy`, `kind-key`, `kind-status`, `kind-rbac-check`, `kind-watchdog-drill`, `kind-rollout-test`, `helm-check`, `kind-down`.
- Scripts: `kind-secrets.sh`, `kind_rbac_check.sh`, `kind_watchdog_drill.py`, `kind_rollout_test.py`, `kind_proxy_check.sh`, `helm_diff.py`, `image_budget.py`.
- Debug knobs for drills: `FXA_DEBUG_STARTUP_DELAY_S` (gateway), `POST /_mock/hog` (mock LLM).
- Runbooks `pod-oomkilled.md` and `watchdog-restart-loop.md`.
- 45 new tests (watchdog policy, canary and API calls; rendered-chart checks).

### Changed
- Draining (API-08): requests on open keep-alive connections are served with `Connection: close` instead of 503.
- The gateway image carries `data/sources.yaml` for the ingestion Job; `FXA_EMBED_CACHE_DIR` lets ingestion use a writable work directory while the model stays baked in.
- `make bootstrap` defaults to Phase 4.

### Fixed during the phase
- Ingestion OOMKilled at 1.5 GiB on kind: embedding batch 16 in the Job (peak about 931 MiB).
- `kind load docker-image` fails with Docker's containerd image store: load single-platform archives instead.
- The rollout test's key check passed against one pod while the other had not refreshed its key snapshot yet (ADR-025): it now requires a run of successes over fresh connections.

## Phase 3: Observability (2026-10-07)

### Added
- OpenTelemetry SDK in the gateway: Prometheus exporter on port 9464, never published (OBS-04); spans for each request (`fxassist.ask`), graph node (`agent.*`) and model call (`llm.chat`, `gen_ai.*` attributes); W3C `traceparent` sent to the model server; optional export to Langfuse Cloud or any OTLP/HTTP endpoint with a bounded queue and 2 s timeout (DEP-03).
- Metrics: request and stage latency histograms (queue, retrieval, TTFT, LLM; OBS-05), decode tokens per second, output tokens, circuit breaker state and openings, active runs, request-log queue; label allowlist (OBS-01).
- Log redaction of API keys, bearer/basic credentials, Langfuse keys and passwords (OBS-02); question and answer text on spans only with `FXA_TRACE_CONTENT=true`.
- Prometheus v3.15.0 and Grafana 13.2.3 in the compose `full` profile; 9 alert rules, each naming a runbook; a 14-panel dashboard generated from `observability/grafana/build_dashboard.py` with a description and "No data" text per panel (OBS-03).
- `make load` (mixed traffic, one key per worker, not a benchmark), `make dashboard`, `make up-mock`.
- Runbook `llm-timeout-storm.md` from a hanging-model drill.
- 40 new tests (288 in total).

### Changed
- `make up` now starts everything, including Prometheus and Grafana (Phase 3 acceptance). `make up-full` is gone; `make up-mock` is the full stack without Ollama. `make bootstrap` defaults to Phase 3.
- The compose mock LLM uses the `ollama` streaming format with a 150 ms first token and 15 ms per token, so dashboards show realistic shapes.
- Grafana memory limit 384m (measured 217 MiB at start).

### Fixed during the phase
- Alert rules for a dead model fired nothing in a 5-minute drill (cache and circuit breaker hid the failure); replaced by alerts on the model-call failure ratio and on the breaker not closing, which fired at about 2 and 3 minutes.
- Calls refused by the open circuit breaker were counted as successful model calls.
- FastAPI 0.142 emits its own `POST /v1/ask` span; the gateway's span was renamed `fxassist.ask`.

## Phase 2: API service (2026-10-06)

### Added
- `services/gateway` (`fxassist-gateway serve | create-key | revoke-key | list-keys`): `POST /v1/ask` with SSE (default) or JSON, `/healthz`, `/readyz`, `/v1/info`. API keys hashed in PostgreSQL and checked from an in-memory snapshot; token-bucket rate limit in Redis (Lua, Redis clock) with a stricter in-memory fallback; answer cache keyed on question, corpus, prompt and model versions; request coalescing; bounded agent queue; request IDs; non-blocking request log; graceful shutdown with draining; the gateway's informational disclaimer on every answer.
- LLM adapter in `fxassist_agent.llm` (used by the CLI, the eval and the gateway): always-streaming, format normalisation, timeouts, retries with backoff and full jitter honouring `Retry-After`, circuit breaker, one retry on empty answers, context-length check, cooperative cancellation, per-call timings.
- `services/mock_llm` (`fxassist-mock-llm`): configurable misbehaving OpenAI-compatible server with runtime config and stats endpoints.
- Dockerfiles for the gateway and mock LLM (multi-stage, non-root, embedding model baked in); both join the compose lite set. `make up` points the gateway at Ollama, `make up-lite` at the mock.
- Make targets `serve`, `mock-llm`, `api-key`, `drill`, `demo` (`LLM=ollama` for the real model).
- `scripts/drill.py`: stops Redis, PostgreSQL and Qdrant one at a time and checks the gateway. `scripts/demo.py`. `bench/guard.py` (CAC-05).
- Runbooks `qdrant-down.md` and `redis-down.md` from the drill.
- Proposed ADR-024 (stream progress, then the validated answer) and ADR-025 (key snapshot), awaiting approval.
- 123 new tests (248 in total, offline): mock features, adapter against the mock and against Ollama when it runs, gateway API, cache, dependencies.

### Changed
- The agent takes a per-request `RunControl` (cancellation, progress) and reports an `error_code`; vector search failures become `store_unavailable` instead of an exception.
- `PROMPT_VERSION` (hash of the prompt texts) added for the cache key.
- Test helpers moved from `conftest.py` to `agent_helpers.py` / `gw_helpers.py` (two `conftest` modules cannot both be imported). Pytest runs with the repo root on `pythonpath`.
- `make bootstrap` defaults to Phase 2.

### Fixed during the phase
- SSE responses sent `Content-Length: 0` (inherited from Starlette's `Response`).
- redis-py's default 3 retries would have slowed every request during a Redis outage; disabled in favour of the gateway's own policy.
- The mock's "hang" mode kept test servers from shutting down; it now ends when the client leaves.

## Phase 1: RAG core (2026-10-06)

### Added
- ADR-022: Ollama runs as a non-root compose container (`llm` profile), with the GPU added by `compose.gpu.yaml` when `nvidia-smi` works. `make up` starts it, `make pull-model` downloads `qwen2.5:3b-instruct`, `make up-lite` leaves it out.
- ADR-023: embeddings through fastembed (ONNX Runtime) instead of PyTorch.
- Corpus of 26 public documents (CFTC, ESMA, ASIC, FCA, Wikipedia) in `data/sources.yaml`, with licence terms read from each publisher; `data/SOURCES.md` generated from it. Three sources excluded because they block non-browser clients or need permission.
- `services/agent` (`fxassist` command): fetch with atomic writes and a report; PDF/HTML/Wikipedia extraction with boilerplate removal and an English-prose check; content-hash chunk IDs; Qdrant collection carrying the embedding model and corpus version; LangGraph agent (guard, retrieve, grade, rewrite, generate, validate); citation and number validation; step and time caps.
- Make targets `fetch`, `ingest`, `ask`, `eval`, `experiment`, `sources-md`, `pull-model`, `up-lite`.
- Evaluation set: 39 questions, 10 injection attacks, 3 planted-excerpt scenarios; scoring script; retrieval experiment.
- 84 offline unit tests for the agent (in-memory Qdrant, fake embedder, scripted fake LLM).

### Changed
- `make bootstrap` now checks Ollama, the model and the GPU as warnings with fix commands (ENV-03), and requires Phase 1 by default.
- Lint: E501 disabled (the formatter wraps code; long lines left are prompts and regexes).

### Fixed during the phase (found by tests or the eval)
- fastembed's `passage_embed` takes `batch_size` as a keyword; the first ingestion failed every document (and proved DAT-07: the run still finished with a report).
- PDF header detection looked at 3 edge lines; the FCA header is 5 lines tall.
- The guard flagged "What is a trading signal?" as an advice request.
- The model sometimes writes "Insufficient context" in prose or as `[Insufficient_context]`; now recognised.
- Injected sentences appended after the last citation (eval items i09, c02) are now removed by the validator, including when the model puts the citation after the full stop.

### Fixed (after Phase 0)
- `make bootstrap` rounded Docker memory down to whole GiB, so a default 8 GB WSL2 VM (reported as 7.7 GiB) was flagged as too small for kind. Tiers now accept 90% of their nominal size and show one decimal (ENV-02).
- `make bootstrap` suggested `make install` even when it had already been run.

## Phase 0: Scaffold (2026-10-06)

### Added
- Repository layout for services, deploy, observability, bench, notebooks, eval, data, docs and results.
- `Makefile` with `help`, `bootstrap`, `install`, `lint`, `fmt`, `test`, `check`, `secrets-scan`, `up`, `up-full`, `down`, `down-volumes`, `ps`, `logs`. `ingest`, `ask`, `eval`, `kind-up`, `kind-deploy` and `demo` are placeholders that name their phase.
- `scripts/bootstrap.sh`: checks prerequisites (WSL2, repo location, git, make, uv, Python 3.11, Docker, Docker memory, disk, Ollama, kind, kubectl, helm) and prints the exact fix for each problem. It never installs anything.
- `uv` workspace root with a locked dev toolchain (ruff, pytest, pre-commit, PyYAML). New ADR-021.
- Pre-commit hooks: gitleaks secret scan, private-key detection, LF enforcement, large-file guard, ruff.
- `deploy/compose/compose.yaml` with Qdrant, Redis and PostgreSQL: pinned versions, non-root, no capabilities, localhost-only ports from env vars, memory limits, healthchecks.
- Minimal GitHub Actions CI: bootstrap on a clean runner, lockfile check, lint, tests, full-history gitleaks scan, compose validation.
- Tests for ENV-01, ENV-02, ENV-04, ENV-05 (bootstrap part), SAF-07 and SAF-08 (compose part).
- Docs: README skeleton, LEARNING (Phase 0), VERSIONS, GLOSSARY, SOURCES placeholder, CHANGELOG.

### Changed from the plan
- A minimal CI workflow was added in Phase 0 instead of Phase 5, because SAF-07 (a Phase 0 row) requires a CI secret scan. Phase 5 still builds the full pipeline.
