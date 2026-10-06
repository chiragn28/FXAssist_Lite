# bench

The GPU-lab benchmark harness and its orchestration (ADR-017). Method and how to read the numbers: `docs/BENCHMARK_METHOD.md`.

| Module | Job |
|---|---|
| `prompts.py` | Fixed short and long (RAG-sized) prompt sets, built from repo files, hashed (BEN-04) |
| `harness.py` | Streaming load generator: TTFT, latency, tokens/s, errors; warm-up discarded, rows fsync'd and resumable (BEN-01, 05, 06, 08, GPU-05) |
| `matrix.py` | The experiments in priority order, one variable each (BEN-07, BEN-09, GPU-06) |
| `lab.py` | What the notebook calls: environment checks, isolated vLLM virtualenv, downloads with retry, server lifecycle, smoke test, experiments, eval, OOM drill, packaging; `mock` mode for dry runs |
| `report.py` | Result files -> `results/BENCHMARKS.md`; missing numbers are PENDING |
| `guard.py` | Refuses gateway benchmarks while the answer cache is on (CAC-05) |

Tests: `bench/tests`, including the whole lab flow in mock mode.
