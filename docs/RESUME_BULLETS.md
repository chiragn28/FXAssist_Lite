# Resume bullets

Rules (build contract, Phase 8): numbers only if they are measured and in `results/`; no claims of EKS, AWS GPU or production experience. `results/` has no GPU-lab numbers yet, so the bullets below contain **no numbers**, and two lines are marked PENDING: fill them from `results/BENCHMARKS.md` and `results/EVAL_REPORT.md` after the Kaggle runs, or leave them out.

Personal project, zero cost, built with Claude Code as a pair programmer.

1. Built a self-hosted RAG question-answering platform over public forex/CFD regulatory documents: FastAPI gateway with SSE streaming, hashed API keys, Redis token-bucket rate limiting and versioned answer caching, a LangGraph agent over Qdrant with bge-small embeddings, and code-level citation and number validation before any answer is released.
2. Designed and tested a failure policy for every dependency (Redis, PostgreSQL, Qdrant, the LLM): timeouts, retries with jittered backoff, a circuit breaker, request coalescing and graceful shutdown, verified with automated tests against a fault-injecting mock LLM and with drills that stop each service.
3. Instrumented the system with OpenTelemetry (metrics and traces), Prometheus alert rules and a generated Grafana dashboard; reworked LLM alerting after a failure drill showed the original rules stayed silent while caching and the circuit breaker masked a dead model.
4. Packaged the stack as a Helm chart on a local kind cluster with startup/readiness/liveness probes, an HPA and PodDisruptionBudget, zero-failure rolling updates, and a canary watchdog CronJob with restart hysteresis and RBAC scoped to a single Deployment, verified with `kubectl auth can-i`.
5. Built a GitHub Actions pipeline without secrets (lint, tests, image builds with size budgets, Trivy scanning, a kind integration test) and a resumable vLLM benchmark lab for free Kaggle T4 GPUs, after verifying T4 support in vLLM's source code.
6. PENDING: "Benchmarked Qwen2.5-3B FP16 vs AWQ on vLLM (T4): <throughput / TTFT result from results/BENCHMARKS.md>; evaluated citation correctness and abstention accuracy <from results/EVAL_REPORT.md>."

Do not add: local laptop numbers (they are development numbers from a 4-bit model), "production", "EKS", "AWS", or any GPU figure not in `results/`.
