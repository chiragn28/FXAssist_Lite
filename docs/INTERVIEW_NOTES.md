# Interview notes

How to explain FXAssist Lite, the trade-off behind each decision, what it does not prove, and answers to the follow-up questions it invites. Numbers quoted here are labelled: **local** (laptop, development, not results) or **GPU lab** (from `results/`, Kaggle T4, 2026-10-07).

## The 1-minute version

I built a small but complete LLM platform for one use case: answering questions about forex and CFD trading from 26 public regulator and educational documents, with citations. A FastAPI gateway handles API keys, rate limiting, caching and streaming; a LangGraph agent retrieves from Qdrant, grades the excerpts, generates with a local model, and checks every citation and number before anything reaches the user. It runs on Docker Compose and on a local Kubernetes cluster (kind) with a Helm chart, probes, an autoscaler and a watchdog that restarts the model server when a canary prompt keeps failing. It is instrumented with OpenTelemetry, Prometheus and Grafana. For GPU serving I prepared a vLLM benchmark lab for free Kaggle T4s, built so a killed session loses nothing. Everything was built around a register of 102 failure scenarios, each with a test or a recorded drill. Total cost: zero.

## The 5-minute version

1. **The problem.** Retail traders ask questions like "what leverage can I get on EUR/USD?". The answer must come from regulators' documents, with a source, and the system must never give personal trading advice or invent a number.
2. **RAG core.** Ingestion is a data pipeline: download, extract (PDF, HTML), strip boilerplate, chunk with content-hash IDs so re-running it is idempotent, embed with bge-small on CPU, store in Qdrant with the embedding model recorded on the collection (a mismatch is refused). Chunk size and top-k were chosen by an experiment that measured whether the actual fact was retrieved, not just the right document.
3. **Agent.** A LangGraph state machine: guard (cheap rules for advice requests, injection, vague questions), retrieve, grade, optional rewrite, generate, validate. The validator removes citations to excerpts that were not retrieved, rejects numbers that are not in the cited text, and drops sentences after the last citation, which is where injected text tends to land.
4. **Gateway.** Every dependency has a written failure policy, and each one was broken on purpose: Redis down means no cache and a stricter local rate limit; PostgreSQL down means buffered logs and cached keys; Qdrant down means 503, because answering without documents would be making things up. The LLM adapter has timeouts, retries with jittered backoff, a circuit breaker and stream-break detection, all tested against a mock server that can misbehave on purpose.
5. **Operations.** Metrics separate queue time, retrieval, time to first token and total time. A drill with a dead model showed my first alert rules stayed silent for five minutes, because the cache and the circuit breaker hid the failure from users; the rules now watch the model calls. On kind: zero failed requests across rolling updates (after fixing how draining treated keep-alive connections), RBAC limited to one deployment and proved with `kubectl auth can-i`, and a watchdog with cooldowns so it cannot loop.
6. **GPU lab.** vLLM 0.31.0 on a T4, verified against its source (the T4 gets the Triton attention backend; bfloat16 is not available, so float16 is forced). FP16 vs AWQ, concurrency 1 to 32, short vs RAG-sized prompts, one knob at a time, an induced OOM, tensor parallel on two T4s. **GPU lab:** AWQ gave 1.7x to 2.3x FP16's throughput on short prompts but almost nothing (1.07x to 1.14x) on RAG-sized prompts with many users, because the work shifts from reading weights to prefill; two T4s gave 1.6x to 1.8x; long first-token waits were prefill queueing with the KV cache at most 21% full. The evaluation passed 46/52 (FP16) and 42/52 (AWQ), with every safety test passed and failures being abstentions.

## Decisions and their trade-offs

| ADR | Decision | Trade-off, in one line |
|---|---|---|
| 001 | Forex/CFD documents as the domain | Realistic and regulated (needs refusals, citations), but a small corpus and a narrow eval set |
| 002 | Zero cost | Forces honest scope: no EKS, no paid GPUs; GPU work is a notebook, Kubernetes has no GPU |
| 003 | One OpenAI-compatible interface for every model | Swappable backends and a mock for CI; can't use backend-specific features without leaking them |
| 004 | GPU lab inside a Kaggle notebook | No tunnels or terms-of-service risk, reproducible; but sessions die, so everything must be resumable |
| 005 | Qwen2.5-3B, FP16 and AWQ | Fits a T4 with room for KV cache and gives a fair precision comparison; small model, weaker answers; research licence |
| 006 | vLLM, version verified for T4 first | Best-known serving engine; newer versions drop old-GPU paths, so the version is pinned after reading its source |
| 007 | LangGraph for control flow, LangChain only for splitters | Every step and exit is explicit and testable; more code than a one-call chain |
| 008 | Qdrant (server locally, embedded in the notebook) | Light, named in the JD; one more service to run than pgvector |
| 009 / 023 | bge-small on CPU, through ONNX Runtime | GPU stays free for the LLM, images are small; embeddings must never be mixed across runtimes, so the collection records it |
| 010 | Redis cache and limiter with a failure policy | Fast and shared across replicas; a Redis outage must degrade, not fail, so the fallback is part of the design |
| 011 / 025 | PostgreSQL for keys and logs; keys checked from a memory snapshot | Database outages don't block requests; new or revoked keys take up to 30 s per replica |
| 012 | OpenTelemetry, Prometheus, Grafana; Langfuse optional | Vendor-neutral and free; low-cardinality labels only, detail goes to traces |
| 013 | kind + Helm | Real Kubernetes behaviour locally; no GPU scheduling, no cloud load balancers |
| 014 | Health/readiness probes and a canary watchdog | Self-healing for hangs; needs hysteresis and tight RBAC or it becomes the outage |
| 015 | GitHub Actions without secrets | Fork PRs work; nothing in CI can touch a real model or paid API |
| 016 | Small, honest eval set | Repeatable and cheap; 22 answerable questions is a small sample |
| 017 | Strict benchmark method | Numbers mean something; fewer of them per GPU hour |
| 018 | Security basics, untrusted retrieved text | Layers of cheap checks; does not catch a true number attributed to the wrong thing |
| 019 | WSL2 | Scripts behave like CI; Windows-specific setup steps |
| 020 | Documentation as a deliverable | Slower, but every decision is explainable later |
| 021 | uv and one lockfile for the workspace | Identical installs everywhere; one more tool to install |
| 022 | Ollama as a compose container | Reproducible local model with the GPU passed through; quantised, so local quality is not representative |
| 024 | Stream progress, then the validated answer | Safety checks always run; the user waits for the whole generation before reading the text |

## Honest limits (say these first)

- **GPU work ran on free T4s in a notebook, not on Kubernetes.** Kubernetes work ran on kind on a laptop, without a GPU. **No EKS or AWS GPU experience is claimed.**
- GPU numbers come from one Kaggle session on T4s (3 repetitions per cell, one eval run per model). A T4 is not an H100, and the first GPU eval was invalid (10 documents missing from the index) until a rerun: say so if asked. Every local number is a development number from a 4-bit model on a laptop GPU.
- CI runs green on GitHub (lint, tests, image builds, vulnerability scan, a kind integration test). It needed three fixes on its first runs (a non-existent action tag, a size metric that differs between Docker setups, a bootstrap check that was too strict): it had only been linted locally before. A pull request from a fork has not been tried yet.
- The evaluation set is small (39 questions, 10 attacks, 3 planted scenarios). It found real bugs; it cannot prove general quality.
- Langfuse export is implemented and tested against fake endpoints, not against a real Langfuse account.
- Production troubleshooting experience here means induced failures and drills, not real incidents with real users.

## Likely follow-up questions

**Why not just call the model once with the documents in the prompt?** Retrieval lets the corpus grow beyond the context window, the grader and validator are cheap safety layers, and every step is visible in traces. For 26 documents a long-context call would work too; it would not teach or show the operational parts.

**How do you know the answers are correct?** For answerable questions: retrieval hit rate (an expected document was retrieved), fact hit rate (the retrieved text contains the fact), citation correctness, and output checks in code. The honest gap: a number that is in the cited excerpt but attributed to the wrong thing passes (for example "20:1 for majors" when the excerpt says 20:1 for non-majors). The local 3B model did exactly that. On the GPU, neither model did; their failures were abstentions, plus one AWQ answer that used a citation label in place of the number ("a limit of [S1]"), a gap the checks now document.

**What happens when the model server dies?** Requests time out after 30 s of silence, five consecutive failures open the circuit breaker so further requests fail in milliseconds with 503 and Retry-After, cached answers still work, and alerts fire on the share of failed model calls (about 2 minutes in the drill). On Kubernetes the watchdog restarts the deployment after three failed canaries, at most twice an hour.

**Why did your first alerts not fire?** They were written against user-facing symptoms that the resilience layer was designed to hide: the cache kept answering, and the breaker turned timeouts into fast errors that flipped every 30 seconds. Lesson: alert on the dependency (failed model calls, breaker not closing), and drill alerts like any other code.

**How did you make rolling updates lossless?** maxUnavailable 0, readiness that reflects real readiness, a preStop sleep so kube-proxy removes the pod first, a termination grace longer than the app's drain, and serving requests that arrive on already-open keep-alive connections with `Connection: close`. The first test failed one request in 17 because draining answered those with 503.

**What would you change for production?** Real secrets management (External Secrets or a vault, not a script), Alertmanager routing, kube-state-metrics for OOM alerts, multiple Qdrant replicas, a managed PostgreSQL with retention on the request log, per-tenant rate limits, token-level streaming behind a flag for use cases that accept unvalidated drafts, and GPU nodes with the model server behind the same OpenAI-compatible interface.

**How would you size GPUs for this?** Weights plus KV cache: 36 KiB of KV per token for this model in FP16, so about 6.7 GiB of cache on a T4 holds about 195,000 tokens, roughly 47 requests at the 4,096-token limit. Then measure: throughput against concurrency until it flattens, and keep p95 time to first token under the target. GPU lab: the estimate was close (vLLM reported 6.86 GiB = 199,744 tokens), and the cache never passed 21% use: the limit at 32 users was prefill queueing (TTFT p95 17 s on long prompts), not memory.
