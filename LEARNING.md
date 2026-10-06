# Learning log

One section per phase: the concepts used, why the design choices were made, and interview questions I should now be able to answer. Written for someone who knows data engineering but not LLM infrastructure.

---

## Phase 0: Scaffold

### Concepts and decisions

- **Reproducibility starts with a lockfile.** `pyproject.toml` says which packages I want (`pytest>=9.1.1`). `uv.lock` records the exact version and file hash of every package, including dependencies of dependencies. CI and Docker install from the lockfile with `uv sync --locked`, so "works on my machine" becomes "works on every machine". `uv lock --check` fails if someone edited `pyproject.toml` without re-locking (ADR-021, CI-03).

- **Line endings are a real failure mode on Windows.** Windows uses CRLF (`\r\n`), Linux uses LF (`\n`). A bash script with CRLF fails with `$'\r': command not found`. `.gitattributes` with `eol=lf` makes git write LF no matter how the machine is configured. A test also scans every committed file for CRLF (ENV-01).

- **Pre-commit hooks stop mistakes before they reach history.** Once a secret is in a git commit, deleting the file doesn't remove it: it stays in history and must be rotated. Gitleaks runs on every commit and looks for token patterns. Its hook only sees *staged* changes, so CI also scans the *full history* (SAF-07). I checked that it works by planting a fake GitHub token in a throwaway repo: gitleaks found it and exited with code 1.

- **Containers should not run as root.** If an attacker breaks out of a process running as root inside a container, they hold root's powers. Each compose service sets a non-root `user`, drops all Linux *capabilities* (fine-grained root powers such as binding low ports or changing file owners) and sets `no-new-privileges`. Verified: `CapEff` is all zeros in every container.

- **Healthchecks turn "started" into "ready".** A container can be running while the database inside it is still initialising. Each service has a healthcheck, and `docker compose up --wait` blocks until all report healthy. The same idea becomes Kubernetes readiness probes in Phase 4 (DEP-04, K8S-01).

- **Configuration through environment variables.** Ports and the bind address come from `.env` (ENV-04). This was tested for real: a local PostgreSQL already held port 5432, and setting `FXA_POSTGRES_PORT=55432` fixed it without editing any file. Binding to `127.0.0.1` keeps the services off the local network.

- **Memory budgets have to be explicit.** Every service has a `mem_limit`, so the total RAM the stack needs is known in advance and a runaway process gets killed instead of freezing the laptop. A test checks that the "lite" set fits the documented 4 GiB minimum (ENV-02).

- **Read the release notes of what you pin.** PostgreSQL 18 moved its data directory. A compose file copied from a PostgreSQL 17 tutorial mounts `/var/lib/postgresql/data` and silently loses data on every restart. Checking the official Dockerfile caught this; a restart test confirmed that data now survives.

### Interview questions I can now answer

1. *Why does a lockfile matter for an ML platform, and how do you detect lockfile drift in CI?*
   Without one, the same commit can install different library versions on different days; for fast-moving libraries like vLLM or LangChain, that changes behaviour. Install with `uv sync --locked` and fail CI on `uv lock --check`.
2. *A secret was committed and pushed. What do you do?*
   Rotate the secret first: assume it is compromised. Then remove it from history if needed, and prevent a repeat with a pre-commit scan plus a full-history scan in CI.
3. *What is the difference between a container that is running and one that is ready, and why does it matter for deployments?*
   Running means the process started; ready means it can serve requests. Sending traffic before readiness causes errors during startups and rolling updates. Healthchecks in Compose and readiness probes in Kubernetes gate traffic on readiness.

---

## Phase 1: RAG core

### Concepts and decisions

- **RAG is a data pipeline first.** Most of the work was ingestion: download (with atomic writes so a failed download never leaves half a file), extract text (PDF page by page, HTML main content only, Wikipedia via its API), strip repeated headers and footers, split into chunks, embed, store. It is the same shape as an ETL job: idempotent, resumable, with a report of what failed and why.

- **Stable IDs make ingestion idempotent.** Each chunk's ID is a hash of its document and its text. Running ingestion twice upserts the same IDs, so nothing is duplicated, and only new chunks are embedded: the second run took 7.8 s instead of 2 min 14 s. When a document changes, chunks whose IDs disappeared are deleted, and the *corpus version* (a hash of all chunk IDs) changes. The Phase 2 cache puts the corpus version in its key, so stale answers expire by themselves (ADR-010).

- **Vectors from different models cannot be compared, and nothing warns you.** If the embedding model changes, search still returns results; they are just meaningless. So the collection records the model, runtime and dimension, and the agent refuses to start on a mismatch (DAT-10). Same reason for ADR-023: even the same model run through ONNX instead of PyTorch gives slightly different numbers.

- **Retrieval scores are relative, not absolute.** bge-small scores almost everything in the corpus between 0.7 and 0.9, so a fixed "relevance threshold" barely filters. The relevance decision is made by the grader (a model call), and the threshold only catches clearly off-topic questions (sourdough bread scored 0.42).

- **One long document can crowd out the answer.** "What leverage limits apply to retail CFD clients in the EU?" abstained because a 50-page FCA paper filled all five retrieval slots and the ESMA text with the answer ranked 13th. Capping each document at 2 excerpts fixed it. The experiment below shows the cap helps on exactly that question and costs nothing elsewhere; with one question of evidence, that is a sensible default, not a proven law.

- **An agent is a state machine with exits.** LangGraph makes each step a node and each decision an edge: guard, retrieve, grade, optional rewrite, generate, validate. Every path ends, either with an answer or with a deliberate refusal. Hard caps (12 steps, 120 s) mean even a bug cannot loop forever (RET-09).

- **Safety in three layers, cheapest first.** (1) Rules in code catch vague questions, advice requests, prompt-extraction and obvious injections before any model call: deterministic, free, testable. (2) The prompt treats excerpts and the question as untrusted data inside tags that the data cannot close. (3) Code checks the answer: citations must point to real excerpts, numbers must appear in the cited text, and anything written after the last citation is removed. Layer 3 caught two attacks that layers 1 and 2 missed (eval items i09 and c02).

- **Measure, then change one thing.** The first eval found 7 failures. Each one got a diagnosis before a fix: two were my eval being too strict, two were real bugs in my validator (found only because a real model produced unusual formatting), and the rest are limits of a 4-bit 3B model. Unit tests with a fake LLM check the control flow; the eval with the real model checks behaviour. You need both.

### Experiment: chunk size x top-k (DAT-09)

Retrieval only (no LLM), 22 answerable questions, bge-small via fastembed, overlap 15%, run on 2026-10-06 with `make experiment`. Hit rate: an expected source is in the top-k. MRR: mean of 1/rank of the first excerpt from an expected source. Fact hit rate: the retrieved text contains the expected fact (11 questions with a checkable fact such as "30:1").

| Chunk size (chars) | Chunks | Per-doc cap | Top-k | Hit rate | MRR | Fact hit rate |
|---|---|---|---|---|---|---|
| 500 | 1387 | off | 3 | 95% | 0.88 | 91% |
| 500 | 1387 | off | 5 | 95% | 0.88 | 100% |
| 500 | 1387 | 2 | 3 | 95% | 0.88 | 82% |
| 500 | 1387 | 2 | 5 | 95% | 0.88 | 91% |
| 1000 | 727 | off | 3 | 95% | 0.91 | 100% |
| 1000 | 727 | off | 5 | 95% | 0.91 | 100% |
| 1000 | 727 | 2 | 3 | 95% | 0.91 | 100% |
| **1000** | **727** | **2** | **5** | **100%** | **0.92** | **100%** |
| 1500 | 502 | off | 5 | 95% | 0.89 | 100% |
| 1500 | 502 | 2 | 5 | 95% | 0.89 | 100% |
| 1500 | 502 | 2 | 8 | 100% | 0.89 | 100% |

(Top-k 8 rows omitted where identical to top-k 5; the full table is printed by `make experiment`.)

What it says:
- **500-character chunks cut facts in half**: the fact hit rate drops to 82 to 91% because a sentence like "30:1 for major currency pairs" ends up split from its context.
- **1000 characters ranks best** (MRR 0.92) and keeps every fact. 1500 is close but ranks slightly worse: bigger chunks dilute the match.
- **The per-document cap** only changes one question (a21, the EU one), and that question is why it exists. On 22 questions this is weak evidence; a larger eval set could overturn it.
- My first version of this experiment measured only "right document retrieved" and scored 100% everywhere. **A metric that never moves tells you nothing**; checking for the actual fact made the differences visible.

Defaults chosen: chunk size 1000, overlap 150, top-k 5, at most 2 excerpts per document.

### Local evaluation (development numbers, not results)

`make eval` with Ollama `qwen2.5:3b-instruct` (4-bit) on an RTX 3060 Laptop GPU, 2026-10-06, final code:

| Measure | Result |
|---|---|
| Retrieval hit rate (answerable + ambiguous) | 22/22 |
| Answered (answerable + ambiguous) | 19/22 |
| Citation correctness (of answered) | 18/19 |
| Abstention accuracy (unanswerable, off-domain, vague) | 11/11 |
| Safety (4 advice, 2 reveal, 10 injection attacks) | 16/16 |
| Planted-excerpt scenarios (RET-02, RET-03, SAF-03) | 3/3 |
| Overall | 48/52 |
| Latency p50 / p95 (questions reaching the model) | 0.9 s / 1.5 s |

Cautions, in order of importance:
1. **These are not reported results.** ADR-016: only the GPU lab with the same evaluation counts. This is a 4-bit model on a laptop.
2. **The small model is unreliable on leverage questions.** Across 4 runs, a02, a18, a21 and m01 flipped between pass and fail. Typical error: "ESMA sets 20:1 for major pairs" (it is 30:1; 20:1 is the limit for non-major pairs in the same excerpt). The number check cannot catch this because 20:1 *is* in the cited text. Comparing FP16 and AWQ in Phase 6 will show how much of this is quantisation.
3. **Output depends on what ran before.** a21 failed inside the eval run but passed 6 times out of 6 on its own. The likely cause is Ollama reusing cached prompt prefixes plus non-deterministic GPU arithmetic, even at temperature 0. The eval now records the rejected text so the next failure can be diagnosed.
4. **22 answerable questions is a small sample.** One question moves a rate by about 5 points.

### Interview questions I can now answer

1. *How do you make document ingestion idempotent and keep the vector store in sync when documents change?*
   Derive chunk IDs from content (hash of document ID + chunk text) and upsert by ID. Before ingesting a document, read its existing IDs; after, delete the ones that are no longer produced. Track a corpus version (a hash of all IDs) and put it in cache keys so answers built on old content expire automatically.
2. *Your RAG system gives a confident wrong number. Where do you look, and what can you add?*
   First check whether retrieval found the right text (retrieval hit rate, fact hit rate). If it did, the model misread it, so generation is the problem. Add output checks: citations must point to retrieved excerpts, numbers must appear in the cited excerpt, and uncited claims are removed. Be honest that this catches invented numbers but not a real number attributed to the wrong thing; for that you need a stronger model or a per-claim check.
3. *How do you defend a RAG system against prompt injection?*
   In layers: rule-based checks on the input, a prompt that treats retrieved text and the question as data inside delimiters the data cannot close, and checks in code on the output (validated citations, numbers backed by sources, nothing after the last citation). Test it with a fixed attack set that includes attacks designed to get past the first layer, and measure the result.

---

## Phase 2: API service

### Concepts and decisions

- **A gateway is a chain of cheap checks in front of one expensive call.** Every request passes request ID, API key, body checks, rate limit, cache and coalescing before the model is touched. Each step is ordered by cost: rejecting a bad key costs microseconds, a model call costs seconds. A request that will fail should fail at the cheapest possible step, with a clear status code (401, 413, 422, 429, 503, 504).

- **Every dependency needs a written failure policy, decided before it fails.** Redis down: the cache is skipped (fail open) but the rate limiter falls back to a stricter in-memory limit (never "no limit"). PostgreSQL down: keys come from a memory snapshot and logs wait in a bounded buffer. Qdrant down: 503, because answering without documents would be making things up. `make drill` stops each one for real and checks the behaviour; all three recovered in under 5 s without a restart.

- **Timeouts, retries and circuit breakers work as a set.** A timeout bounds how long one call can hang (LLM-01). Retries hide short blips, but only for errors that are safe to repeat (connection refused, 429, 503), with *exponential backoff and full jitter* so that many clients do not retry in lockstep (LLM-04). A *circuit breaker* stops calling a server that keeps failing, so an overloaded model gets room to recover and users get a fast 503 instead of a slow one. Retrying a timeout or a half-finished stream is deliberately not done: it doubles load at the worst moment.

- **Streaming is about the connection, not only the tokens.** The adapter always streams from the model, even though the user sees the answer only after validation (ADR-024). Streaming gives: time to first token, the ability to stop generation when the user leaves (closing the connection makes the model server stop), and a way to tell "the stream broke" from "the model finished" (a stream that ends without `[DONE]` or `finish_reason` is an error, not a short answer).

- **Sync code inside an async server needs a bridge.** The agent is synchronous (LangGraph `invoke`), FastAPI is async. The agent runs in a fixed-size thread pool, behind a semaphore with a timeout: that is a bounded queue, and "busy" becomes a fast 503 instead of an unbounded pile-up. A thread cannot be killed from outside, so cancellation is cooperative: a flag the agent checks between graph nodes and the adapter checks between chunks.

- **The cache key decides correctness, not just speed.** Key = hash of (normalised question, corpus version, prompt version, model). Re-ingesting documents, editing a prompt or switching models changes every key, so stale answers are never served; they simply expire (CAC-02). The prompt version is a hash of the prompt text, so nobody has to remember to bump it. Errors are never cached; "I don't know" is cached for only 2 minutes (CAC-03).

- **Identical concurrent requests should share one computation.** Without *request coalescing*, a popular question arriving 50 times during a 2 s generation costs 50 generations, because the cache is only filled at the end (a *cache stampede*). The first request starts the run; the others wait for it. The run belongs to the group, not to the first client, so the first client hanging up does not fail the others; it is cancelled only when everyone has left (API-04, LLM-05).

- **Graceful shutdown has three parts.** Stop accepting new connections, let in-flight requests finish within a grace period, and tell the load balancer early (readiness turns to `shutting_down`). Tested for real: `docker compose stop gateway` during an 8 s request, and the request still finished with 200. Compose's `stop_grace_period` (35 s) must be longer than the app's own grace (30 s), or Docker kills it mid-answer.

- **A mock that cannot misbehave tests nothing.** The mock LLM can add latency, delay the first token, fail with 429/503 and `Retry-After`, return empty answers, send malformed chunks, drop the connection mid-stream, hang, and imitate the format quirks of Ollama, vLLM and others. Its "ollama" format was copied from the real server's output with `curl`, not from documentation. One contract test runs the same adapter against real Ollama to check the mock has not drifted.

### What the tests found

- My SSE response inherited a `Content-Length: 0` header from Starlette's base class, so the first streamed byte broke the protocol. Streaming responses must not declare a length.
- redis-py 8 retries 3 times with backoff by default. During a Redis outage that would have added seconds to every request; the gateway now turns it off and applies its own policy.
- The drill created an API key and used it immediately: 401. The key snapshot refreshes every 30 s (ADR-025), which is the designed trade-off; the scripts now wait for the key. Worth knowing as an operator: new and revoked keys take up to 30 s.
- Two `conftest.py` files in different test folders both exported helpers; Python can only have one module called `conftest`. Helpers now live in `agent_helpers.py` and `gw_helpers.py`.

### Measured locally (development numbers, not results)

2026-10-06, compose stack with Ollama `qwen2.5:3b-instruct` on an RTX 3060 Laptop GPU:

| What | Value |
|---|---|
| First answer after the gateway started (host run, from the request log) | 2.3 s total: retrieval 444 ms, time to first token 344 ms, 85 tokens generated |
| Answer in the compose demo (`make demo LLM=ollama`) | 1.6 s |
| Same question again (cache hit) | 0.1 s |
| Off-topic question (no model call) | 0.09 s |
| Gateway memory after the demo | 347 MiB (limit 1 GiB) |
| Cold start of the lite stack (`make down && make up-lite`) | ready in about 9 s, 0 restarts |
| Recovery after Redis / PostgreSQL / Qdrant restart | 3.4 s / 1.2 s / 1.1 s |

### Interview questions I can now answer

1. *Your LLM backend starts returning 503s under load. What should the API in front of it do?*
   Retry a small, bounded number of times with exponential backoff and full jitter, honouring `Retry-After`. Count consecutive failures; past a threshold, open a circuit breaker and fail fast with 503 and `Retry-After` for a cooldown, then let one trial request through. Do not retry timeouts or half-finished streams, and do not cache errors. Expose the error counts and breaker state as metrics.
2. *How do you design a cache for LLM answers so it never serves a stale answer?*
   Put everything the answer depends on in the key: the normalised question, the corpus version, the prompt version (a hash of the prompt text) and the model identity. Changing any of them creates new keys; old ones expire. Cache only successful answers, give "I don't know" a short TTL, never cache errors, and make the cache fail open. Turn it off for benchmarks.
3. *A user closes the browser halfway through a long generation. What happens in your system?*
   The SSE response notices the disconnect, closes its event stream, and leaves the request group. If nobody else is waiting for the same answer, the run is cancelled: the agent stops at its next step, and the adapter closes the connection to the model server at the next chunk, which makes the server stop generating. The thread slot is released when the thread really ends, so the concurrency limit stays honest. A test checks the mock sees the cancelled stream and no runs are left.

---

## Phase 3: Observability

### Concepts and decisions

- **Three signals, three jobs.** *Metrics* are numbers over time, cheap to keep and to alert on ("how many timeouts per second?"). *Traces* follow one request through every step ("where did this slow request spend its time?"). *Logs* are the detailed record of events ("what exactly failed, for request 3f2a...?"). The request ID links logs to the request log row; the trace ID links spans across services.

- **OpenTelemetry is the vendor-neutral layer.** The code creates counters, histograms and spans through the OTel API. Which backend receives them (Prometheus for metrics, Langfuse or any OTLP endpoint for traces) is configured in one place, at startup. Without configuration the API calls do nothing, so the CLI and the tests pay nothing. It also meant the Phase 2 call sites did not change in Phase 3.

- **Prometheus pulls, and labels are a budget.** Prometheus scrapes `/metrics` every 5 s. Every distinct combination of label values is a separate time series kept in memory, so a label like `user_id` or `question` can create millions of series and take Prometheus down (*high cardinality*). Labels here come from fixed lists (route, status, outcome, error code, stage), and a test checks real traffic against that list (OBS-01). Per-user detail belongs in logs and traces.

- **Histograms, not averages.** Latency is recorded in buckets (5 ms ... 120 s). Prometheus computes percentiles from bucket counts with `histogram_quantile`. p95 shows what the slowest 1 in 20 users get; an average hides them. Bucket edges matter: a p95 can only be as precise as the buckets around it.

- **Measure each stage on its own (OBS-05).** Total time mixes queueing, retrieval and generation. Time to first token is measured inside the LLM adapter, from sending the generation request to the first token, so it is not inflated by waiting for a slot or searching Qdrant. A test proves it: a 0.4 s first-token delay in the mock shows up as about 0.4 s of TTFT, while retrieval stays under 0.2 s.

- **Tracing must never hurt the request (DEP-03).** Spans are queued in memory and exported by a background thread, with a 2 s timeout and a 512-span queue. If Langfuse is down, slow or returns 429, spans are dropped, not requests. Tests run requests against a refused, a rate-limited and a hanging trace backend and compare latency with a baseline. Traces also cross service boundaries: the adapter sends a W3C `traceparent` header, so a model server that traces joins the same trace.

- **Alert on the dependency, not just on users.** The first set of alert rules looked sensible and stayed silent for 5 minutes while the model was completely dead (drill below). The cache kept answering popular questions, and the circuit breaker converted timeouts into fast "circuit open" errors that cycled every 30 s. The alerts that work watch the model calls themselves: the share of failed calls, and "the breaker has not closed once in 2 minutes".

- **Dashboards and alerts are code.** The dashboard JSON is generated from a Python spec in which every panel has a query, a plain explanation (its tooltip) and a "No data" text that says what an empty panel means (OBS-03). Tests check that the JSON matches the spec, that every query uses a metric that exists, and that every alert names a runbook file that exists.

### The dashboard, panel by panel

| Panel | Query (simplified) | How to read it |
|---|---|---|
| Requests per second by outcome | `sum by (outcome) (rate(fxa_requests_total[1m]))` | Traffic split by what happened. `none` = rejected before the agent ran (401, 422, 429). A rise in `abstained` with steady traffic points at retrieval. |
| Errors per second by code | `sum by (code) (rate(fxa_errors_total[1m]))` | Each code maps to one HTTP status. `rate_limited` during `make load` is the limiter working, not a fault. |
| Fallback rate | abstained + out_of_scope / all agent outcomes | How often the agent could not answer from the documents. In the load it was about 3%. |
| Cache hit ratio | hits / (hits + misses + skips) | Skips (Redis down) count as misses, so an outage shows as a drop. It was 89% in the load because the question pool repeats. |
| Request latency p50 / p95 | `histogram_quantile` over `fxa_request_duration_seconds_bucket{cached="false"}` | Real answer time, with cache hits left out because they would hide it. With the mock: p50 0.85 s, p95 1.8 s. |
| Stage latency p95 | the same, by `stage` | Where the time goes: queue, retrieval, ttft, llm. In the load: queue 5 ms, retrieval 50 ms, TTFT 0.24 s, all model calls 1.75 s. The model dominates, as expected. |
| Generation speed | p50 of `fxa_llm_tokens_per_second` | Decode speed after the first token. The mock is set to 15 ms per token, so it shows about 70; Ollama and vLLM report real numbers. |
| Tokens generated per second | `rate(fxa_llm_output_tokens_total[1m])` | Total output load on the model, answers vs grader. |
| LLM calls by result | `rate(fxa_llm_calls_total[1m])` by kind and result | Every model call and how it ended. |
| Model call failure ratio | failed calls / all calls | The signal that catches a dead model even when the cache and breaker hide it from users. |
| Circuit breaker state | `max(fxa_llm_circuit_state)` | 0 closed, 1 half-open, 2 open. Flipping between 1 and 2 means trial calls keep failing. |
| Agent runs in progress | `fxa_agent_runs_active` | Worker threads busy. At the limit (4), new requests queue. |
| Context truncations and coalesced requests | rates of both counters | Truncations: excerpts dropped to fit the context window (RET-06). Coalesced: requests that shared another's answer (API-04). Usually "No data", which is fine. |
| Dependency failures and degraded modes | Redis/PostgreSQL/Qdrant failures, local rate limiting, dropped log rows | All zero in normal operation; anything here means a fallback is active. |

### Drill: the model hangs (2026-10-07)

Compose stack with the mock set to accept requests and never answer, `make load` with 3 workers. Timeline from Prometheus, sampled every 15 s:

| Time after the model died | What happened |
|---|---|
| 0 to 30 s | Requests needing the model wait 30 s, then 504. Cached questions are still answered. |
| ~1 min | 5 consecutive failures: circuit breaker opens; requests get 503 in milliseconds. |
| every 30 s | Breaker goes half-open, one trial call, times out, opens again. |
| ~2 min 15 s | **FxaLLMFailing** fires. |
| ~3 min | **FxaLLMCircuitNotClosing** fires. |
| model healed | First request answered within 10 s (the half-open trial), breaker closed, both alerts resolved within 43 s, no restart. |

The first version of the rules ("circuit open for 1 minute", "timeouts above 3 per minute", "5xx above 5% of requests") fired **nothing** in the same 5 minutes. The drill also found a bug: calls refused by the open breaker were counted as successful model calls. Both are fixed, with a regression test, and the runbook `docs/runbooks/llm-timeout-storm.md` records the whole timeline.

### Measured locally (development numbers, not results)

| What | Value |
|---|---|
| Memory, whole compose stack without Ollama (`make up-mock`, after load) | gateway 349 MiB, Grafana 217 MiB, Qdrant 94 MiB, Prometheus 59 MiB, PostgreSQL 48 MiB, mock 45 MiB, Redis 13 MiB |
| 5-minute hanging-model drill | 746 requests: 365 answered (almost all from the cache), 9 timeouts (504), 10 fast circuit-open 503s, the rest rate-limited or guard outcomes |

### Interview questions I can now answer

1. *What is metric cardinality and how do you keep it under control?*
   The number of distinct label combinations, each of which is a separate time series in Prometheus's memory. Keep labels to small fixed sets (status, route template, error code), never user IDs, prompts or request IDs; put those in logs and traces. Enforce it with an allowlist and a test that checks the labels real traffic produces.
2. *How would you measure time to first token correctly in a RAG service?*
   Measure it where the model call happens: from sending the generation request to the first content token, with streaming on. Record queue time, retrieval time and total time as separate histograms, so a slow total can be attributed. Prove it with a test that injects a known first-token delay and checks only TTFT moves.
3. *Your LLM backend died but no alert fired. Why could that happen, and what would you alert on?*
   Caching and circuit breakers protect users and hide the failure from user-facing metrics: cached answers keep succeeding, and the breaker turns slow timeouts into fast, intermittent errors that "open for N minutes" rules miss because of half-open trials. Alert on the dependency itself: the failure ratio of model calls, and "the breaker has not closed in N minutes". Then drill it: break the model on purpose and check that the alert fires.

---

## Phase 4: Containers, Helm, kind, reliability

### Concepts and decisions

- **A Helm chart is templated YAML plus a values file.** Templates hold the structure (Deployments, Services, probes); `values.yaml` holds every setting in one place; an environment file overrides only what differs (kind: 3 keys, CI: 9 keys). `make helm-check` renders the chart for each environment and prints the diff, so "works on kind, broken in CI" differences are visible in review (K8S-09). Helm 4 is a new major version: `--atomic` became `--rollback-on-failure`, and `--wait` now uses kstatus to decide "ready".

- **Requests and limits are different promises.** A *request* is what the scheduler reserves for the pod; a *limit* is where the kernel steps in: CPU over its limit is throttled, memory over its limit is killed (OOMKilled, exit code 137). Limits came from measurements: the gateway uses about 350 MiB, so it requests 512 Mi and is killed at 1 Gi. The first deploy proved the point unplanned: ingestion was OOMKilled three times at 1.5 GiB. The fix was the cause (embedding batch 64 -> 16, peak about 931 MiB), not a bigger limit.

- **Three probes, three questions.** *Startup*: has it finished starting? (protects slow starts; liveness waits until it passes). *Readiness*: should it get traffic now? (dependencies included, so a pod that lost Qdrant leaves the Service). *Liveness*: is it stuck and should be restarted? (process only, never dependencies, or a database blip restarts every pod). Tested on kind: a 60 s slow start survives a 180 s startup budget with 0 restarts, and is killed twice in 150 s with a 30 s budget.

- **Zero-downtime rollouts take four pieces together.** `maxUnavailable: 0` (start a new pod before stopping an old one), readiness (traffic only to pods that are ready), a `preStop` sleep (keep serving a few seconds while kube-proxy removes the pod from the Service), and the app's own draining. The first rollout test still failed one request in 17: my "draining" mode answered 503 to requests arriving on an already-open keep-alive connection. Serving them with `Connection: close` (so the client reconnects to another pod) fixed it: 0 failures in 3 runs in a row.

- **RBAC is least privilege you can prove.** The watchdog's Role names one resource: `get` and `patch` on `deployments/fxassist-mock-llm`. `make kind-rbac-check` asks the API server 17 questions as the watchdog (`kubectl auth can-i --as=...`): it can restart its one Deployment; it cannot read secrets, list pods, touch the gateway or act in another namespace. Every other pod has `automountServiceAccountToken: false`, so it holds no API credentials at all.

- **A watchdog needs hysteresis, or it becomes the outage.** Restarting on the first failure turns a slow model into a restart loop that never finishes loading. Rules: warn first, restart only after 3 consecutive failures, then a 10-minute cooldown and a cap of 2 restarts per hour, after which a human must look. The decision is a pure function, so a test can simulate a whole broken hour in milliseconds. On kind, the drill showed exactly one restart, then "suppressed" when the new pod was broken again. CronJob pods are fresh each run, so the state lives in an annotation on the watched Deployment.

- **Secrets never pass through Helm values.** Values files are committed and end up in `helm get values`, so the chart only references a Secret by name, and a script creates it from the local `.env` (K8S-06). A test fails if any values key looks like a password, token or key.

- **kind is real Kubernetes in a container, with its own quirks.** Images must be loaded into the node (no registry), and `kind load docker-image` broke with Docker's newer containerd image store; exporting one platform with `docker save --platform` and `kind load image-archive` works. kind writes its own kubeconfig file here, so the laptop's other kubectl contexts are untouched.

### Measured on kind (2026-10-07, one node, WSL2, no GPU)

| What | Value |
|---|---|
| Full ingestion Job (26 downloads, 727 chunks) inside `helm upgrade --wait --wait-for-jobs` | 5 min; peak memory about 931 MiB with batch 16 |
| Rolling restart of 2 gateway pods under traffic | 12-18 s; 0 failed requests in 3 consecutive runs |
| Watchdog: broken model to restart | 3 CronJob runs (about 2 min), then cooldown held |
| Mock LLM OOMKilled -> ready | 8 s |
| SSE through nginx 1.30.5 (default buffering) | first progress event 0.05-0.15 s, answer 0.65-0.76 s: streamed |
| Images (compressed) | gateway 234 MB, mock LLM 51 MB, watchdog 47 MB |

### Interview questions I can now answer

1. *A pod keeps restarting. How do you find out why, and what if its logs are gone?*
   `kubectl describe pod` and `get pod -o jsonpath` for `lastState.terminated.reason` and exit code (137 = SIGKILL, usually OOMKilled), plus events. If the pod was deleted (a failed Job), the node's kernel log still records cgroup OOM kills with the process's memory use. Then fix the cause (here, batch size), not just the limit.
2. *How do you roll out a new version without dropping requests?*
   maxUnavailable 0 and a surge pod, a readiness probe that reflects real readiness, a preStop sleep so endpoint removal propagates before SIGTERM, a grace period longer than the app's drain time, and an app that finishes in-flight work and sends `Connection: close` on open keep-alive connections. Then prove it: traffic during `kubectl rollout restart`, count failures.
3. *You want an automatic "restart the model server when it hangs" job. What could go wrong, and how do you make it safe?*
   Restart loops (the restart does not fix the cause, or the model needs longer to load than the check allows), false positives from slow but healthy models, and a job with permissions to break everything. Use a canary with content and latency checks, N consecutive failures, a cooldown, an hourly cap that hands over to humans, structured logs of every decision, and RBAC limited to the one Deployment, verified with `kubectl auth can-i`.

---

## Phase 5: CI/CD and GPU lab preparation

### Concepts and decisions

- **Verify the platform before you pin it.** "vLLM supports the T4" was true and not enough. Reading the installed 0.31.0 source showed which attention kernel a T4 actually gets: FlashAttention needs compute capability 8.0, and FlashInfer is switched off on the T4's 7.5 on purpose ("currently broken on SM75"), so it falls to the Triton backend. Its `--help` confirmed every flag the notebook passes, and that `--dtype auto` would choose bfloat16 for this model, which a T4 cannot run. None of this needed a GPU, and all of it would have cost GPU hours to discover the hard way.

- **Free GPU time is the scarce resource, so the notebook is built like a batch job.** Every result row is appended and `fsync`ed the moment it exists; every stage logs its outcome; finished work is skipped on a rerun; a half-written last line is ignored. A killed session costs only the repetition that was running. Experiments run in priority order inside an hour budget, so a short session still produces the most important numbers.

- **Dry-run everything that does not need the GPU.** The notebook cells only call `bench/lab.py`, and that module has a mock mode: the same code starts the mock LLM instead of vLLM, runs a scaled-down matrix, packs a zip and builds a report. It runs in the test suite and as `make lab-dry-run`. Typos, wrong paths and broken resume logic show up on the laptop, not on Kaggle.

- **A benchmark is a controlled experiment.** Warm-up requests are thrown away (first requests pay for compilation and caches). Output length is fixed (`max_tokens` plus `ignore_eos`), otherwise "tokens per second" compares answers of different lengths. Prompts are fixed and hashed. Three repetitions, median and range. Errors are counted and kept out of latency, or a server that fails fast looks fast. Each experiment changes one setting, enforced in code. FP16 and AWQ get identical load.

- **Know where GPU memory goes.** Weights take a fixed share (FP16 6.2 GB, AWQ 2.7 GB); the rest of vLLM's budget, minus activations, becomes KV cache. For this model one token of KV cache is 2 x 36 layers x 2 KV heads x 128 x 2 bytes = 36 KiB, so about 6.7 GiB holds about 195,000 tokens: roughly 47 requests of 4,096 tokens at once. AWQ's smaller weights buy KV cache, which is why it can matter for throughput even when it is not faster per token. The report reads the real numbers from vLLM's own startup log.

- **CI that a stranger can run.** No secrets anywhere, so a pull request from a fork passes too; read-only token; `pull_request`, never `pull_request_target`, which would run a stranger's code with write access. Tools come from official releases with SHA-256 checks rather than third-party actions, because a compromised action tag runs inside your pipeline. A test enforces these rules on the workflow files.

- **Scan, then fix the cause.** The first Trivy scan found two HIGH CVEs in every image. Both were in the Python base image's own `setuptools` and `wheel`, which nothing uses at runtime. Removing pip, setuptools and wheel from the final stage fixed them all (0 after) and shrank the attack surface. The scan reports instead of blocking: a new CVE in a base image should become a reviewed version bump, not a red build on an unrelated change.

- **Disks fill up; know what your database does then.** A PostgreSQL drill on a tiny volume showed both behaviours: a full table file makes one statement fail while the server stays up; a full write-ahead log makes it stop on purpose rather than risk corruption, and crash recovery replays the log once there is room. Room means at least one 16 MB WAL segment, which a 12 MB "ballast" file did not provide.

### Interview questions I can now answer

1. *How would you benchmark an LLM server so the numbers mean something?*
   Call the model server directly, cache off. Fixed, hashed prompt sets (short and RAG-sized), fixed output length, warm-up discarded, at least three repetitions with median and spread, p50 and p95 for TTFT and latency, errors counted separately, one knob per experiment, every setting recorded in every row. Then say what it does not show: a T4 is not an H100.
2. *How do you estimate whether a model and a workload fit on a GPU?*
   Weights (parameters x bytes per parameter, or the safetensors size) plus KV cache (2 x layers x KV heads x head size x bytes per token, times tokens in flight) plus activation and runtime overhead, all under `gpu_memory_utilization`. Then confirm with the server's own startup log (KV cache size, maximum concurrency at the max length) and an induced OOM to see the failure mode.
3. *What makes a CI pipeline safe to run on pull requests from forks?*
   No secrets needed at all; `pull_request` with a read-only token, never `pull_request_target` for untrusted code; tools pinned and checksum-verified; no external paid services; everything mockable. Plus a test or lint that keeps it that way.

---

## Phase 8: Documentation and evidence

### Concepts and decisions

- **A fresh clone is the real test of "reproducible".** Cloning the repo into an empty directory and following the README (`make bootstrap`, `make install`, `cp .env.example .env`, `make demo`) worked end to end in under 3 minutes with brand-new volumes, after one fix: the PostgreSQL port was taken by a Windows program that Linux tools cannot see. Only Docker itself can tell whether it can publish a port, so `make bootstrap` now asks it, ignores ports held by FXAssist's own containers, and prints the exact `.env` change.

- **Evidence beats claims, so the documents say which is which.** Every number in the interview notes, README and resume bullets is either labelled as a local development number or comes from `results/`; GPU numbers are PENDING until the Kaggle runs. The resume bullets currently contain no numbers at all, on purpose. The architecture's coverage matrix now has a dated "status today" column beside the original targets.

- **Check that the design and the code still agree.** Re-reading the ADRs against the code found one gap: ADR-011 promised evaluation history in PostgreSQL, and nothing wrote it. `make eval-record` now does, and was run against a real database.

- **Some problems stay open, and that is written down.** About 30% of full test runs take ~60 s longer to exit after every test has passed. Timing every exit callback and pytest hook ruled those out; the main thread is sleeping during late interpreter shutdown, probably inside a native extension. It costs CI time, not correctness, so it is recorded under CI-01 rather than hidden or "fixed" with a forced exit.

### Interview questions I can now answer

1. *How do you know your project actually works on someone else's machine?*
   Clone it fresh, follow only the README, with new volumes and no local state, and fix whatever breaks in the scripts or docs rather than on the machine. Here that found a host port conflict that the prerequisite check now detects.
2. *How do you keep a portfolio project honest?*
   Separate targets from evidence, date the status, mark unmeasured numbers PENDING, label development numbers, and list the limits first. Every claim should point to a file: a test, a drill log, a result.
