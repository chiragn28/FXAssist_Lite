# Resume bullets

Rules (build contract, Phase 8): numbers only if they are measured and in `results/`; no claims of EKS, AWS GPU or production experience. Every number in bullet 6 is from the Kaggle GPU lab of 2026-10-07 (`results/BENCHMARKS.md`, `results/tensor_parallel.md`, `results/EVAL_REPORT.md`); the other bullets contain no numbers.

Personal project, zero cost, built with Claude Code as a pair programmer.

1. Built a self-hosted RAG question-answering platform over public forex/CFD regulatory documents: FastAPI gateway with SSE streaming, hashed API keys, Redis token-bucket rate limiting and versioned answer caching, a LangGraph agent over Qdrant with bge-small embeddings, and code-level citation and number validation before any answer is released.
2. Designed and tested a failure policy for every dependency (Redis, PostgreSQL, Qdrant, the LLM): timeouts, retries with jittered backoff, a circuit breaker, request coalescing and graceful shutdown, verified with automated tests against a fault-injecting mock LLM and with drills that stop each service.
3. Instrumented the system with OpenTelemetry (metrics and traces), Prometheus alert rules and a generated Grafana dashboard; reworked LLM alerting after a failure drill showed the original rules stayed silent while caching and the circuit breaker masked a dead model.
4. Packaged the stack as a Helm chart on a local kind cluster with startup/readiness/liveness probes, an HPA and PodDisruptionBudget, zero-failure rolling updates, and a canary watchdog CronJob with restart hysteresis and RBAC scoped to a single Deployment, verified with `kubectl auth can-i`.
5. Built a GitHub Actions pipeline without secrets (lint, tests, image builds with size budgets, Trivy scanning, a kind integration test) and a resumable vLLM benchmark lab for free Kaggle T4 GPUs, after verifying T4 support in vLLM's source code.
6. Benchmarked Qwen2.5-3B FP16 vs AWQ 4-bit on vLLM on free Kaggle T4 GPUs: AWQ gave 1.7x to 2.3x the throughput on short prompts but only 1.07x to 1.14x on RAG-sized prompts with 16 to 32 users; tensor parallelism over two T4s gave 1.6x to 1.8x; vLLM metrics showed long first-token waits were prefill queueing (KV cache at most 21% full, 0 preemptions), not memory. On a 52-item evaluation the FP16 model passed 46 (AWQ 42): every safety and injection test and every should-not-answer question passed, and failures were abstentions rather than invented answers.

Do not add: local laptop numbers (they are development numbers from a 4-bit model), "production", "EKS", "AWS", or any GPU figure not in `results/`.
