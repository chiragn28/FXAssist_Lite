# FXAssist Lite

A zero-cost, self-hosted LLM platform that answers questions over public forex/CFD documentation, with citations. Built to learn and demonstrate LLMOps skills: vLLM serving and GPU tuning, RAG with Qdrant, LangGraph, observability, Kubernetes with Helm, and reliability engineering.

> **Status: Phase 0 (scaffold) complete.** Nothing answers questions yet. See [Project status](#project-status).

## Honest limits

- **GPU work runs on free NVIDIA T4 GPUs in a Kaggle notebook**, not on Kubernetes.
- **Kubernetes work runs on kind (a local cluster) without a GPU.**
- **No EKS or AWS GPU experience is claimed** from this project.
- Local answers use a small Ollama model or a mock server; only GPU-lab runs produce reported numbers.
- Total spend is $0: no paid cloud, no paid APIs.

## Architecture

```mermaid
flowchart TB
    subgraph LOCAL[Laptop: Docker Compose and kind]
        C[Client / load script] --> API[FastAPI gateway]
        API --> RD[(Redis: cache + rate limit)]
        API --> PG[(PostgreSQL: logs, keys, eval history)]
        API --> AG[LangGraph RAG agent]
        AG --> EM[bge-small embeddings on CPU]
        AG --> QD[(Qdrant)]
        AG --> LLM{{OpenAI-compatible LLM URL}}
        LLM --> OL[Ollama small model: dev]
        LLM --> MK[Mock LLM server: CI and kind]
        API --> OT[OpenTelemetry]
        OT --> PR[Prometheus]
        PR --> GR[Grafana]
        AG -.optional.-> LF[Langfuse cloud free tier]
        WD[Watchdog CronJob on kind] --> MK
    end
    subgraph LAB[Kaggle notebook: free GPU lab, self-contained]
        V[vLLM on T4 GPU] --> B[Benchmark and eval harness]
        B --> R[results CSV and markdown]
    end
    R -->|downloaded and committed| LOCAL
```

Design and every decision (ADR-001 to ADR-021): [ARCHITECTURE.md](ARCHITECTURE.md). Failure scenarios and their tests: [EDGE_CASES.md](EDGE_CASES.md).

## Quickstart

The project targets **WSL2 on Windows** (or native Linux). Commands below run inside the WSL2 Ubuntu shell.

### 1. One-time machine setup (Windows)

```powershell
# PowerShell
wsl --install -d Ubuntu-24.04
```

Then in Docker Desktop: **Settings > Resources > WSL integration**, enable Ubuntu-24.04.

### 2. Clone into the Linux filesystem

Keep the repo in your Linux home (`~`), not under `/mnt/c` and not in OneDrive: file access is much faster, and permissions and line endings behave.

```bash
cd ~
git clone <repo-url> fxassist_lite
cd fxassist_lite
```

### 3. Check prerequisites, install, run

```bash
make bootstrap      # checks everything, prints the exact fix for anything missing
make install        # creates .venv from uv.lock, installs the git hooks
cp .env.example .env
make up             # starts Qdrant, Redis and PostgreSQL, waits until healthy
make test
make down
```

`make help` lists every target. Targets for later phases (`ingest`, `ask`, `eval`, `kind-up`, `kind-deploy`, `demo`) say which phase builds them and exit with code 2.

### Port conflicts

Every host port is set in `.env` (ENV-04). If one is taken, change it there. Example: a local PostgreSQL already on 5432:

```bash
FXA_POSTGRES_PORT=55432
```

Services are published on `127.0.0.1` only, so they are not reachable from your network.

### Memory (ENV-02)

| Profile | Starts | Docker memory |
|---|---|---|
| lite (`make up`) | data stores; the gateway and agent from Phase 2 | 4 GiB minimum |
| full (`make up-full`) | lite plus Prometheus and Grafana (Phase 3) | 6 GiB |
| kind (Phase 4) | the whole stack inside a local Kubernetes cluster | 8 GiB |

These budgets are estimates and will be replaced with measurements in Phases 3 and 4. Measured so far (2026-10-06, idle): Qdrant 76 MiB, PostgreSQL 35 MiB, Redis 6 MiB.
To give WSL2 more memory, add `[wsl2]` / `memory=8GB` to `%UserProfile%\.wslconfig`, then run `wsl --shutdown`.

## Project status

| Phase | Scope | Status |
|---|---|---|
| 0 | Scaffold, tooling, bootstrap, data-store compose, CI skeleton | Done |
| 1 | RAG core: ingestion, Qdrant, LangGraph agent, eval set | Not started |
| 2 | FastAPI gateway, cache, rate limit, LLM adapter, mock LLM | Not started |
| 3 | Observability: OpenTelemetry, Prometheus, Grafana, Langfuse | Not started |
| 4 | Containers, Helm, kind, watchdog | Not started |
| 5 | Full CI/CD, Kaggle notebooks, GPU playbook | Not started |
| 6 | GPU session 1 (single T4) | Not started |
| 7 | GPU session 2 (2x T4, tensor parallel) | Not started |
| 8 | Final documentation and evidence | Not started |

## Results

**PENDING.** No benchmark or evaluation has been run. Numbers will appear here only after real runs ([results/](results/)).

## Repository layout

| Path | What lives there |
|---|---|
| `services/gateway` | FastAPI API: auth, rate limit, cache, streaming (Phase 2) |
| `services/agent` | LangGraph RAG agent (Phase 1) |
| `services/mock_llm` | Configurable fake OpenAI-compatible server (Phase 2) |
| `services/watchdog` | Canary CronJob that restarts a broken LLM deployment (Phase 4) |
| `deploy/compose` | Docker Compose for the local stack |
| `deploy/helm` | Helm chart for kind (Phase 4) |
| `observability/` | Prometheus config and Grafana dashboards (Phase 3) |
| `bench/` | Benchmark harness (Phase 5) |
| `notebooks/` | Kaggle GPU-lab notebooks (Phase 5) |
| `eval/` | Evaluation questions and scoring (Phase 1) |
| `data/` | Source list and fetch scripts; raw documents are not committed |
| `scripts/` | Helper scripts, including `bootstrap.sh` |
| `docs/` | Versions, glossary, runbooks, playbooks, interview notes |
| `results/` | Real benchmark and eval output only |
| `tests/` | Repo-level tests |

## Documentation

- [LEARNING.md](LEARNING.md): what each phase taught, in plain language, with interview questions
- [docs/VERSIONS.md](docs/VERSIONS.md): every pinned version and how it was verified
- [docs/GLOSSARY.md](docs/GLOSSARY.md): plain-language definitions
- [CHANGELOG.md](CHANGELOG.md)
