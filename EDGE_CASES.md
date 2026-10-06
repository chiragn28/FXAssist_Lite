# FXAssist Lite: Edge Case Register

A living checklist. Every row must end up with a status: `TODO`, `DONE (test name)`, or `WONT (reason)`. Claude Code must keep the Status column current and reference the IDs in tests and commit messages.

How to read a row: **Scenario** is what goes wrong, **Expected** is the required behaviour, **Test** is how to prove it.

---

## DAT: Data and ingestion

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| DAT-01 | PDF has no extractable text (scanned) | Skip with a logged warning and an entry in an ingestion report; never embed empty chunks | fixture scanned PDF | TODO |
| DAT-02 | Same document ingested twice | Idempotent: no duplicate chunks (stable chunk IDs from content hash) | run `make ingest` twice, compare counts | TODO |
| DAT-03 | Document updated after ingestion | Corpus version changes, old chunks removed, cache invalidated automatically | change a file, re-ingest, check version | TODO |
| DAT-04 | Very large document | Ingested in streaming batches without exhausting memory; chunk count capped with a warning | synthetic 500-page fixture | TODO |
| DAT-05 | Tables, headers, footers, page numbers pollute text | Boilerplate stripped or tolerated; retrieval eval does not regress | manual sample plus eval | TODO |
| DAT-06 | Non-English or mixed-language text | Detected and either skipped or flagged; never silently mis-embedded | fixture | TODO |
| DAT-07 | Download fails or URL has moved | Ingestion continues with others, report lists failures, no partial files left | simulate 404 | TODO |
| DAT-08 | Source licence forbids redistribution | Raw files not committed; only the URL and fetch script are stored | check `.gitignore` and `data/SOURCES.md` | TODO |
| DAT-09 | Chunk size too small or too large | Documented experiment shows effect on hit rate; default justified by data | experiment table in LEARNING.md | TODO |
| DAT-10 | Embedding model changes | Collection records model name and dimension; mismatch is detected at startup and refused | swap model, expect clear error | TODO |

## RET: Retrieval and the agent

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| RET-01 | No chunk passes the relevance threshold | Return "not enough information in the documents", no LLM guess | unanswerable question set | TODO |
| RET-02 | Retrieved chunks contradict each other | Answer cites both and states the conflict, or abstains | crafted pair of chunks | TODO |
| RET-03 | Retrieved text contains instructions ("ignore previous instructions") | Treated as data, not as instructions; answer unaffected | prompt-injection fixture | TODO |
| RET-04 | Question is vague or one word | Agent asks for clarification or returns a low-confidence answer, never fabricates | fixture | TODO |
| RET-05 | Question is off-domain (cooking, jokes) | Politely declined as out of scope | fixture | TODO |
| RET-06 | Retrieved context exceeds the model's maximum length | Context trimmed by relevance rank to fit; a metric counts truncations | long-context fixture | TODO |
| RET-07 | Grader node returns malformed output | Safe default (treat as not relevant) and a logged error, not a crash | mock bad grader output | TODO |
| RET-08 | Citation refers to a chunk that was not retrieved | Detected and removed or the answer is rejected | validator unit test | TODO |
| RET-09 | Agent loops (retry cycle) | Hard cap on graph steps and total time | recursion-limit test | TODO |
| RET-10 | Top-k returns near-duplicate chunks | De-duplicated before generation | unit test | TODO |

## LLM: Generation and the model endpoint

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| LLM-01 | LLM endpoint times out | Request fails with a clear 504 after a bounded timeout, metric incremented, no hanging connection | mock with delay | TODO |
| LLM-02 | Stream breaks halfway | Client receives an explicit error event; partial answers are never cached | mock disconnect | TODO |
| LLM-03 | LLM returns an empty answer | Treated as failure, one bounded retry, then a clear error | mock empty | TODO |
| LLM-04 | LLM returns HTTP 429 or 503 | Bounded retries with backoff and jitter; circuit breaker opens after repeated failure | mock error codes | TODO |
| LLM-05 | Client disconnects mid-stream | Upstream generation cancelled; no leaked tasks or connections | disconnect test, check open connections | TODO |
| LLM-06 | Prompt plus requested output exceeds max model length | Rejected or trimmed before sending, with a precise message | long-prompt test | TODO |
| LLM-07 | Different servers give different token streams (format quirks) | Adapter normalises chunk format; tests run against mock and Ollama | contract test | TODO |
| LLM-08 | Model refuses or adds disclaimers | Passed through unchanged; not treated as an error | fixture | TODO |
| LLM-09 | Mock server is too perfect | Mock supports configurable latency, errors, slow-first-token and malformed chunks | mock feature test | TODO |

## API: Gateway behaviour

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| API-01 | Missing, malformed or wrong API key | 401 with no detail leaked; constant-time comparison on hashes | unit tests | TODO |
| API-02 | Rate limit exceeded | 429 with `Retry-After` | burst test | TODO |
| API-03 | Empty, huge or non-UTF-8 question | 422 for invalid, hard size cap for huge | fuzz test | TODO |
| API-04 | Concurrent identical requests (stampede) | One computation, others wait or are served from cache (request coalescing) | concurrency test | TODO |
| API-05 | Streaming through proxies buffers output | SSE headers set to disable buffering; documented | manual check | TODO |
| API-06 | Request ID missing | Generated, returned in the response, attached to logs and traces | test | TODO |
| API-07 | Slow client (backpressure) | Server doesn't buffer unlimited data; stream cancelled after a timeout | slow-reader test | TODO |
| API-08 | Graceful shutdown during in-flight requests | In-flight requests finish within a grace period; new ones rejected | SIGTERM test | TODO |

## CAC: Cache and rate limiter

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| CAC-01 | Redis is down | Cache skipped (fail open); rate limit uses a conservative in-memory fallback; `/readyz` reports degraded, not failed | stop Redis | TODO |
| CAC-02 | Corpus, prompt or model version changes | Cache key changes, so stale answers are never served | version bump test | TODO |
| CAC-03 | Error or abstention answers | Errors never cached; abstentions cached only with a short TTL | test | TODO |
| CAC-04 | Cache poisoning via crafted question | Key includes normalised question only; no user-controlled key parts beyond the hash | review plus test | TODO |
| CAC-05 | Cache on during benchmarks | Benchmark harness refuses to run with the cache enabled | guard test | TODO |
| CAC-06 | Clock skew or Redis restart resets counters | Documented; limiter errs on allowing slightly more, not locking everyone out | note | TODO |

## DEP: Dependency failures

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| DEP-01 | Qdrant is down | `/readyz` fails; requests get 503 with a clear message; no hallucinated answers | stop Qdrant | TODO |
| DEP-02 | PostgreSQL is down | Requests still succeed; logs buffered (bounded) or dropped with a metric | stop Postgres | TODO |
| DEP-03 | Langfuse unreachable or rate-limited | Requests unaffected; tracing exporter times out quickly and drops | block network to Langfuse | TODO |
| DEP-04 | Services start in the wrong order | Retries with backoff; readiness gates traffic; no crash loops | `docker compose up` cold start | TODO |
| DEP-05 | Disk full on a volume | Clear error, alert metric, no corrupted data | note and manual test | TODO |
| DEP-06 | Hugging Face download fails in notebook | Retry, resume partial downloads, fail with an actionable message | simulate offline | TODO |

## OBS: Observability

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| OBS-01 | High-cardinality labels (user ID, question text) | Forbidden as metric labels; lint check or test | label allowlist test | TODO |
| OBS-02 | Sensitive data in logs or traces | API keys and full prompts redacted or truncated; PII-safe by default | log scan test | TODO |
| OBS-03 | Dashboard has no data | Panels show "no data" clearly, and every panel has a documented query | review | TODO |
| OBS-04 | Metrics endpoint unauthenticated | Exposed only inside the compose network or cluster, not publicly | config review | TODO |
| OBS-05 | Time to first token measured wrongly (includes queueing or retrieval) | Metrics separate retrieval, queue, TTFT and total time | test with mock delays | TODO |

## K8S: Kubernetes and Helm (kind)

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| K8S-01 | Slow startup makes liveness kill the pod | Startup probe protects slow starts; documented values | start-delay test | TODO |
| K8S-02 | Pod OOMKilled | Resource limits and requests set; alert query documented; runbook written | memory-hog test | TODO |
| K8S-03 | Watchdog restart loop | Needs N consecutive failures plus a cooldown; max restarts per hour | failure-injection test | TODO |
| K8S-04 | Watchdog false positive (slow but healthy) | Latency threshold and canary prompt tuned; warning before action | test | TODO |
| K8S-05 | Watchdog permissions too broad | RBAC limited to one named deployment | `kubectl auth can-i` checks | TODO |
| K8S-06 | Secrets in values files | Placeholders only; real values from local env or a Secret created by script | grep check in CI | TODO |
| K8S-07 | Rolling update drops requests | Readiness gates and PodDisruptionBudget verified during an update | rollout test | TODO |
| K8S-08 | kind image not found | Documented `kind load docker-image` step in Makefile | fresh-clone test | TODO |
| K8S-09 | Helm values drift between environments | One base values file plus small overrides; `helm template` diff checked | CI | TODO |

## CI: Pipeline

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| CI-01 | Flaky tests (timing, ports) | No fixed sleeps; use readiness polling and random free ports | repeat 10 times | TODO |
| CI-02 | Pull request from a fork | No secrets required; all jobs pass | fork PR | TODO |
| CI-03 | Dependency drift | Locked dependencies; a scheduled weekly job reports updates | lockfile check | TODO |
| CI-04 | Docker build cache stale or huge images | Multi-stage builds; image size budget checked | CI size check | TODO |
| CI-05 | Anything in CI needs a GPU or paid API | Forbidden; CI is fully mockable | review | TODO |

## GPU: Kaggle lab

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| GPU-01 | vLLM version doesn't support T4 | 10 minute smoke test first; pin a compatible version; record in `docs/VERSIONS.md` | smoke test cell | TODO |
| GPU-02 | Model defaults to bfloat16 | Set float16 explicitly; startup fails loudly if the dtype is unsupported | config check | TODO |
| GPU-03 | Out-of-memory on load or under load | Documented memory formula; knob table (memory utilization, max length, max sequences); runbook | induced OOM drill | TODO |
| GPU-04 | Library conflicts with preinstalled packages | Isolated virtual environment; pinned versions | clean-notebook run | TODO |
| GPU-05 | Session killed or times out | Every result row is written to disk immediately; run is resumable from a checkpoint file | kill mid-run | TODO |
| GPU-06 | Weekly GPU hours exhausted | Session plan with hour budget; priority order of runs; cut stretch goals first | plan in `docs/KAGGLE_PLAYBOOK.md` | TODO |
| GPU-07 | Only one GPU assigned instead of two | Multi-GPU run skipped with a documented note; nothing crashes | check device count | TODO |
| GPU-08 | NCCL or peer-to-peer problems on 2 GPUs | Record the exact error, try documented environment workarounds once, then stop and write it up | tensor-parallel attempt | TODO |
| GPU-09 | Internet disabled in the notebook | Setup cell detects and tells me to enable it (and verify the account if required) | check cell | TODO |
| GPU-10 | Disk quota exceeded by model files | Download only needed files; clean caches; check free space first | disk check cell | TODO |
| GPU-11 | Notebook outputs leak tokens (Hugging Face) | Tokens via notebook secrets only; never printed; output cells reviewed before publishing | review | TODO |
| GPU-12 | Colab used as fallback behaves differently | Notebook parametrised; differences noted | optional | TODO |

## BEN: Benchmark validity

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| BEN-01 | Cold start skews results | Warm-up requests discarded | harness check | TODO |
| BEN-02 | Cache or prefix caching inflates numbers | Cache off; prefix caching reported as an explicit variable | config recorded in every result row | TODO |
| BEN-03 | Single run noise | At least 3 repetitions; report median and spread | harness | TODO |
| BEN-04 | Prompt set differs between runs | Fixed prompt file with hash recorded | hash in results | TODO |
| BEN-05 | Averages hide tail latency | Report p50 and p95 | harness | TODO |
| BEN-06 | Errors counted as fast successes | Error rate reported; failed requests excluded from latency but shown separately | harness | TODO |
| BEN-07 | Several knobs changed at once | One variable per experiment; experiment log table | review | TODO |
| BEN-08 | Output length varies, so tokens per second misleads | Fix max output tokens; report generated token counts | harness | TODO |
| BEN-09 | Unfair comparison FP16 vs AWQ | Same model, same prompts, same settings, separate runs | review | TODO |

## SAF: Safety, security and domain

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| SAF-01 | User asks "should I buy EUR/USD now?" | Declines personalised trading or investment advice; offers general documented information only | fixture | TODO |
| SAF-02 | User asks for guaranteed returns or profit tips | Declines; cites risk warnings from the documents if available | fixture | TODO |
| SAF-03 | Answer states a number (leverage limit, fee) not in the sources | Numbers must be supported by a cited chunk; otherwise abstain | eval check | TODO |
| SAF-04 | Prompt injection in user input | System instructions hold; test set of 10 attacks | adversarial set | TODO |
| SAF-05 | Request to reveal system prompt or keys | Refused | fixture | TODO |
| SAF-06 | Every answer must be framed as informational | Standard disclaimer added by the gateway, not by the model | test | TODO |
| SAF-07 | Secrets committed to git | Pre-commit secret scan and CI scan | scan job | DONE (pre-commit gitleaks: test_saf07_precommit_runs_gitleaks; CI full-history scan: test_saf07_ci_scans_full_history; manual: planted token caught). First real CI run pending a push to GitHub |
| SAF-08 | Container runs as root or image has known vulnerabilities | Non-root user; vulnerability scan with a free scanner reported, not blocking | CI | TODO: partial. Done: compose services non-root with all capabilities dropped (test_saf08_runs_as_non_root). Left: our own images (Phase 4), vulnerability scan (Phase 5) |

## ENV: Developer environment

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| ENV-01 | Windows line endings break shell scripts | `.gitattributes` forces LF for scripts | fresh-clone test | DONE (test_env01_gitattributes_forces_lf, test_env01_no_crlf_in_tracked_text_files; bootstrap also checks the checkout) |
| ENV-02 | Docker Desktop memory too low for the full stack | Documented minimum; a "lite" compose profile for lower-RAM machines | check | TODO: partial. Done: bootstrap memory check (test_env02_docker_memory_tiers), minimums in README, test_env02_memory_limit_set, test_env02_lite_profile_fits_budget. Left: the `full` profile has nothing in it until Phase 3 |
| ENV-03 | Ollama not running or model not pulled | Clear startup error with the exact fix command | test | TODO |
| ENV-04 | Port conflicts on the host | Ports configurable by environment variables | test | DONE (test_env04_ports_come_from_env, test_env04_every_variable_is_documented; manual 2026-10-06: host Postgres held 5432, FXA_POSTGRES_PORT=55432 worked) |
| ENV-05 | Fresh clone doesn't work | `make bootstrap` followed by `make demo` works on a clean machine; verified in CI where possible | clean clone test | TODO: partial. Done: `make bootstrap` (tests/test_bootstrap.py, CI step on a clean runner). Left: `make demo` (Phase 2 onwards, fresh-clone test in Phase 8) |
