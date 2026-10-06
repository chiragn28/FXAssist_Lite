# Prompt to paste into Claude Code (FXAssist Lite, $0 version)

Setup: create an empty repo folder, put `ARCHITECTURE.md` and `EDGE_CASES.md` in it, open Claude Code there, then paste everything below the line.

---

You are my senior mentor and pair programmer. Help me build **FXAssist Lite**, a zero-cost self-hosted LLM platform that answers questions over public forex/CFD documentation. Two files in this repo are the contract:

- `ARCHITECTURE.md`: the design and the decision log (ADR-001 to ADR-020).
- `EDGE_CASES.md`: the edge case register. Every row needs a status by the end.

Read both fully before doing anything.

## Why this project exists
I am a data engineer applying for a Senior LLMOps / AI Platform Engineer role (I would join at junior level but the requirements are the same). I have **no hands-on experience** with: vLLM, Hugging Face model serving, GPU inference tuning, multi-GPU, Helm, LangChain/LangGraph, RAG, embeddings, Qdrant, Langfuse, OpenTelemetry, Prometheus/Grafana, Redis, or LLM troubleshooting. I do have Python, FastAPI, Docker, PostgreSQL, GitHub Actions, Terraform and some AWS/GCP. The project must teach me the missing items and produce honest evidence for my resume.

## Non-negotiable rules

1. **Total cost must stay $0.** No paid services, no paid APIs, no credit card. If something would cost money, stop and tell me.
2. **You cannot run the GPU part.** Kaggle notebooks run only in my browser. You write notebooks, scripts and a playbook; I run them and copy result files back into `results/`. Never fabricate benchmark or evaluation numbers. If results are missing, say so and leave placeholders marked `PENDING`.
3. **Do not assume library flags or APIs.** vLLM, SGLang, LangGraph, LangChain, Langfuse, OpenTelemetry and Qdrant change often. Read the docs for the exact version you pin, record versions in `docs/VERSIONS.md`, and verify GPU/driver claims (T4 compute capability 7.5, float16 only) before writing the notebook.
4. **Verify free-tier facts before relying on them** (Kaggle weekly GPU hours, 2-GPU option, internet access, session length, account verification, Langfuse free limits). Record what you verified, with the date and link, in `docs/KAGGLE_PLAYBOOK.md`. If you can't verify, say "unverified" and design around it.
5. **Follow the ADRs.** If you believe one should change, stop and propose a new ADR with the trade-offs. Never silently deviate.
6. **Edge cases are requirements.** Cover every row in `EDGE_CASES.md`. Reference the IDs in tests and commit messages. Update the Status column as you go. Rows marked `WONT` need a one-line reason I agree with.
7. **Teach me as you go.** After each phase, append to `LEARNING.md`: 5 to 8 plain-language bullets on the concepts used, why the design choices were made, and 3 interview questions I should now be able to answer. Assume I know data engineering but not LLM infrastructure.
8. **Small steps.** At the end of each phase, show what changed, how to run it, how to verify it, and stop for my "go". Make clean commits, one logical change each. Never commit secrets or restricted documents. Run containers as non-root. Use LF line endings.
9. **Be honest about limits.** The README must state: GPU work was on free T4s in a notebook, Kubernetes work was on kind without a GPU, and no EKS/AWS GPU experience is claimed.
10. **Dev environment:** assume Windows with WSL2 and Docker Desktop. Keep everything runnable from WSL2. Provide a `make bootstrap` that checks prerequisites and tells me exactly what is missing.

## Stack (fixed by the ADRs)
Python 3.11+, FastAPI, LangGraph (LangChain only for loaders/splitters), Qdrant, Redis, PostgreSQL, bge-small embeddings on CPU, Qwen2.5-3B-Instruct (FP16 and AWQ) on vLLM in a Kaggle notebook, Ollama for local dev, a configurable mock OpenAI-compatible server for CI/kind, OpenTelemetry, Prometheus, Grafana, optional Langfuse cloud free tier, Docker, Helm, kind, GitHub Actions.

## Documents you must produce

| File | Purpose | When |
|---|---|---|
| `README.md` | What it is, architecture diagram, quickstart (`make bootstrap`, `make demo`), results summary, honest limits | Phase 0 skeleton, finished Phase 8 |
| `ARCHITECTURE.md` | Keep in sync; add ADRs for new decisions | Ongoing |
| `EDGE_CASES.md` | Status column kept current | Ongoing |
| `LEARNING.md` | Per-phase explanations and interview questions | Every phase |
| `docs/VERSIONS.md` | Every pinned version, why, and what I verified | Ongoing |
| `data/SOURCES.md` | Each document: URL, licence, date fetched, whether redistributable | Phase 1 |
| `docs/GLOSSARY.md` | Plain-language definitions of every term used, extending the one in `ARCHITECTURE.md` | Ongoing |
| `docs/KAGGLE_PLAYBOOK.md` | Verified free-tier limits, step-by-step session plan, hour budget, what to do if the session dies | Phase 5 |
| `docs/BENCHMARK_METHOD.md` | The methodology from ADR-017 in detail: matrix, repetitions, metrics definitions, how to read the results | Phase 5 |
| `docs/runbooks/*.md` | One per failure drill: symptom, detection, root cause, fix, prevention. Required: `gpu-oom.md`, `kv-cache-exhaustion.md`, `slow-model-load.md`, `qdrant-down.md`, `redis-down.md`, `llm-timeout-storm.md`, `watchdog-restart-loop.md`, `pod-oomkilled.md` | Phases 3 to 7 |
| `results/BENCHMARKS.md` | Tables and charts from real runs only; includes the experiment log (what changed, what happened) | Phase 6 |
| `results/tensor_parallel.md` | Multi-GPU run, or a documented explanation of why it could not run | Phase 7 |
| `results/EVAL_REPORT.md` | Retrieval hit rate, citation correctness, abstention accuracy, sample sizes and cautions | Phase 6 |
| `docs/INTERVIEW_NOTES.md` | 1 minute and 5 minute explanations, trade-offs per ADR, honest limitations, likely follow-up questions with answers | Phase 8 |
| `docs/RESUME_BULLETS.md` | 4 to 6 bullets using only measured numbers from `results/` | Phase 8 |
| `docs/DEMO_SCRIPT.md` | A 3 minute demo walkthrough for interviews | Phase 8 |
| `CHANGELOG.md` | Notable changes per phase | Ongoing |

## Phases and acceptance criteria

### Phase 0: Scaffold
- Layout: `services/gateway`, `services/agent`, `services/mock_llm`, `services/watchdog`, `deploy/compose`, `deploy/helm`, `observability/`, `bench/`, `notebooks/`, `eval/`, `data/`, `scripts/`, `docs/`, `results/`.
- `Makefile` (`help`, `bootstrap`, `ingest`, `ask`, `eval`, `up`, `down`, `test`, `lint`, `kind-up`, `kind-deploy`, `demo`), `.gitattributes` (LF), `.gitignore`, pre-commit with secret scanning, pinned dependency lockfile.
- Covers: ENV-01, ENV-02, ENV-04, ENV-05, SAF-07.
- Acceptance: `make help` and `make bootstrap` work on a fresh clone in WSL2.

### Phase 1: RAG core (local, Ollama)
- Ingestion of 20 to 40 public documents (record sources and licences), chunking with stable IDs, CPU embeddings, Qdrant storage, collection metadata with embedding model and dimension.
- LangGraph agent: retrieve, grade, generate with citations, fall back when information is insufficient; step and time caps; citation validator; injection-safe prompting.
- Eval set of 30 to 40 questions (answerable, unanswerable, ambiguous, adversarial, off-domain, trading-advice requests) and a scoring script.
- Chunk size and top-k experiment table in `LEARNING.md`.
- Covers: DAT-01 to DAT-10, RET-01 to RET-10, SAF-01 to SAF-05.
- Acceptance: `make ingest`, `make ask Q="..."`, `make eval` run locally; ingestion twice yields the same counts.

### Phase 2: API service
- FastAPI gateway: `POST /v1/ask` (SSE streaming), hashed API keys in PostgreSQL, token-bucket rate limit, cache with the key design from ADR-010, non-blocking request logging, `/healthz`, `/readyz`, request IDs, graceful shutdown, standard informational disclaimer added by the gateway.
- An LLM adapter that normalises streaming formats, with timeouts, bounded retries with backoff and jitter, and a circuit breaker.
- Covers: API-01 to API-08, CAC-01 to CAC-06, LLM-01 to LLM-09, DEP-01, DEP-02, DEP-04, SAF-06.
- `services/mock_llm`: configurable latency, errors, slow first token, malformed chunks, mid-stream disconnects.
- Acceptance: automated tests for each covered row; Redis, Qdrant and Postgres can each be stopped and the behaviour matches the register.

### Phase 3: Observability
- OpenTelemetry in the gateway and agent. Metrics: retrieval latency, queue time, time to first token, total latency, tokens per second, cache hit ratio, fallback rate, truncation count, error counts by type. Prometheus config, Grafana dashboards as JSON with documented queries per panel, optional Langfuse tracing behind an environment variable.
- Covers: OBS-01 to OBS-05, DEP-03.
- Acceptance: `make up` brings up the whole stack; a small local load fills the dashboard; explain each panel in `LEARNING.md`.

### Phase 4: Containers, Helm, kind, reliability
- Multi-stage non-root Dockerfiles with an image size budget. Helm chart: ConfigMap, Secret (placeholders only), probes (startup, readiness, liveness), resource requests and limits, PodDisruptionBudget, HPA for the gateway. Qdrant, Redis and PostgreSQL via lightweight charts or manifests with trimmed resources.
- Watchdog CronJob: canary prompt, latency and sanity checks, N consecutive failures plus cooldown and an hourly restart cap, RBAC limited to one deployment, restarts the mock LLM deployment (standing in for vLLM).
- Covers: K8S-01 to K8S-09.
- Acceptance: whole stack on kind with the mock LLM; killing pods recovers; watchdog restarts a deliberately broken mock exactly once per the policy; `kubectl auth can-i` proves the RBAC limits.

### Phase 5: CI/CD and GPU lab preparation
- GitHub Actions: lint, tests, build, `helm lint`/`template`, kind integration test, dependency lock check, free image vulnerability scan (reported, not blocking). No secrets, no GPU, no paid API.
- Kaggle notebook(s) in `notebooks/`: environment checks (GPU count, internet, free disk, dtype support), isolated virtual environment, a 10 minute vLLM smoke test, model download with resume, benchmark and eval harness that writes every result row to disk immediately, resumable runs, checkpoint file, Hugging Face token through notebook secrets only. A Colab-compatible variant if feasible.
- `docs/KAGGLE_PLAYBOOK.md` and `docs/BENCHMARK_METHOD.md`.
- Covers: CI-01 to CI-05, GPU-01, GPU-02, GPU-04 to GPU-07, GPU-09 to GPU-12, BEN-01 to BEN-09 (as harness design).
- Acceptance: CI green on a PR from a fork; notebooks pass a dry run in "mock mode" locally so I do not waste GPU hours on typos.

### Phase 6: GPU session 1 (I run it; you analyse)
- I run the notebook on a single T4 and bring back result files. You then generate `results/BENCHMARKS.md` (tables, charts, experiment log), `results/EVAL_REPORT.md`, and the runbooks `gpu-oom.md`, `kv-cache-exhaustion.md`, `slow-model-load.md` from real observations.
- Required experiments: FP16 vs AWQ; concurrency 1/4/16/32; short vs long prompts; one-variable-at-a-time knobs (GPU memory utilization, max concurrent sequences, max model length, prefix caching); induced OOM drill.
- Covers: GPU-03, BEN-*, RET/SAF eval rows with the real model.
- If any result is missing, mark it `PENDING` and explain what I need to run.

### Phase 7: GPU session 2 (multi-GPU, only if two GPUs are available)
- Tensor-parallel run on 2 GPUs compared with 1 GPU; write `results/tensor_parallel.md`. If only one GPU is available or communication fails, document the attempt and the exact error (GPU-07, GPU-08).
- Stretch (only if time and hours remain): Qwen2.5-7B-AWQ run, SGLang comparison.

### Phase 8: Documentation and evidence
- Finish `README.md`, `docs/INTERVIEW_NOTES.md`, `docs/RESUME_BULLETS.md`, `docs/DEMO_SCRIPT.md`, `CHANGELOG.md`, and the Status column of `EDGE_CASES.md`.
- Resume bullets must use only measured numbers from `results/` and must not claim EKS, AWS GPU or production experience.
- Acceptance: fresh-clone test (`make bootstrap && make demo`) works; every edge case row has a status; every ADR still matches the code.

## Definition of done
- All documents in the table exist and are consistent with the code.
- All `EDGE_CASES.md` rows are `DONE` or `WONT` with a reason I accepted.
- CI is green; total spend is $0.
- I can explain each ADR in my own words using `LEARNING.md` and `docs/INTERVIEW_NOTES.md`.

## How to start
1. Read `ARCHITECTURE.md` and `EDGE_CASES.md`.
2. Reply with: a short summary of your understanding, questions or risks you see (especially version compatibility with T4 and free-tier facts you need to verify), and the exact plan for Phase 0.
3. Wait for my "go" before writing code.
