# FXAssist Lite: Edge Case Register

A living checklist. Every row must end up with a status: `TODO`, `DONE (test name)`, or `WONT (reason)`. Claude Code must keep the Status column current and reference the IDs in tests and commit messages.

How to read a row: **Scenario** is what goes wrong, **Expected** is the required behaviour, **Test** is how to prove it.

---

## DAT: Data and ingestion

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| DAT-01 | PDF has no extractable text (scanned) | Skip with a logged warning and an entry in an ingestion report; never embed empty chunks | fixture scanned PDF | DONE (test_dat01_scanned_pdf_is_skipped_with_warning) |
| DAT-02 | Same document ingested twice | Idempotent: no duplicate chunks (stable chunk IDs from content hash) | run `make ingest` twice, compare counts | DONE (test_dat02_ingesting_twice_adds_no_duplicates, test_dat02_chunk_ids_are_stable_and_source_specific; real corpus: 2 runs, same 731 chunks and corpus version, 0 re-embedded) |
| DAT-03 | Document updated after ingestion | Corpus version changes, old chunks removed, cache invalidated automatically | change a file, re-ingest, check version | DONE (test_dat03_updated_document_replaces_old_chunks_and_changes_version; real corpus: 84 stale chunks replaced, version changed). The gateway cache key includes the corpus version, so re-ingesting changes every key (test_cac02_reingest_means_no_stale_answer) |
| DAT-04 | Very large document | Ingested in streaming batches without exhausting memory; chunk count capped with a warning | synthetic 500-page fixture | DONE (test_dat04_large_document_streams_and_is_capped, test_dat04_embedding_happens_in_bounded_batches) |
| DAT-05 | Tables, headers, footers, page numbers pollute text | Boilerplate stripped or tolerated; retrieval eval does not regress | manual sample plus eval | DONE (test_dat05_repeated_headers_footers_and_page_numbers_are_removed, test_dat05_html_keeps_main_content_only; real FCA/ASIC headers checked). Limitation: header matching ignores digits |
| DAT-06 | Non-English or mixed-language text | Detected and either skipped or flagged; never silently mis-embedded | fixture | DONE (test_dat06_language_check, test_dat06_non_english_document_is_flagged_not_embedded). Note: the check also drops non-prose (respondent lists, formulas): 4 of 731 real chunks |
| DAT-07 | Download fails or URL has moved | Ingestion continues with others, report lists failures, no partial files left | simulate 404 | DONE (test_dat07_failures_are_reported_and_leave_no_partial_files, test_dat07_missing_and_corrupt_files_do_not_stop_ingestion) |
| DAT-08 | Source licence forbids redistribution | Raw files not committed; only the URL and fetch script are stored | check `.gitignore` and `data/SOURCES.md` | DONE (test_dat08_real_registry_loads_and_every_source_has_licence_terms, test_dat08_raw_documents_are_git_ignored, test_dat08_sources_md_lists_every_source; 2 FCA documents fetch-only) |
| DAT-09 | Chunk size too small or too large | Documented experiment shows effect on hit rate; default justified by data | experiment table in LEARNING.md | DONE (`make experiment`; table in LEARNING.md Phase 1: 1000-char chunks, top-k 5, cap 2) |
| DAT-10 | Embedding model changes | Collection records model name and dimension; mismatch is detected at startup and refused | swap model, expect clear error | DONE (test_dat10_mismatched_embedding_model_is_refused, test_dat10_missing_collection_tells_you_to_ingest) |

## RET: Retrieval and the agent

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| RET-01 | No chunk passes the relevance threshold | Return "not enough information in the documents", no LLM guess | unanswerable question set | DONE (test_ret01_nothing_relevant_abstains_without_calling_the_model_to_answer; eval u01-u05 5/5) |
| RET-02 | Retrieved chunks contradict each other | Answer cites both and states the conflict, or abstains | crafted pair of chunks | DONE (test_ret02_prompt_requires_conflicts_to_be_stated; eval scenario c01 with the real model). Note: real-data item m01 is unstable with the local 3B model |
| RET-03 | Retrieved text contains instructions ("ignore previous instructions") | Treated as data, not as instructions; answer unaffected | prompt-injection fixture | DONE (test_ret03_excerpt_cannot_break_out_of_its_data_block, test_ret03_injected_sentence_after_the_last_citation_is_removed; eval scenario c02) |
| RET-04 | Question is vague or one word | Agent asks for clarification or returns a low-confidence answer, never fabricates | fixture | DONE (test_ret04_vague_question_asks_for_clarification, test_guards_flag_unsafe_or_vague_questions; eval v01-v03) |
| RET-05 | Question is off-domain (cooking, jokes) | Politely declined as out of scope | fixture | DONE (test_ret05_off_domain_question_is_declined_before_any_model_call; eval o01-o03) |
| RET-06 | Retrieved context exceeds the model's maximum length | Context trimmed by relevance rank to fit; a metric counts truncations | long-context fixture | DONE (test_ret06_context_is_trimmed_to_fit_the_model_window). Truncations counted per answer, logged and exported as fxa_context_truncations_total (dashboard panel, Phase 3) |
| RET-07 | Grader node returns malformed output | Safe default (treat as not relevant) and a logged error, not a crash | mock bad grader output | DONE (test_ret07_malformed_grader_output_is_treated_as_not_relevant, test_ret07_wrongly_shaped_json_is_handled) |
| RET-08 | Citation refers to a chunk that was not retrieved | Detected and removed or the answer is rejected | validator unit test | DONE (test_ret08_citation_to_unknown_excerpt_is_removed, test_ret08_answer_with_only_invalid_citations_is_rejected) |
| RET-09 | Agent loops (retry cycle) | Hard cap on graph steps and total time | recursion-limit test | DONE (test_ret09_step_limit_stops_the_graph, test_ret09_time_limit_stops_the_graph) |
| RET-10 | Top-k returns near-duplicate chunks | De-duplicated before generation | unit test | DONE (test_ret10_near_duplicate_chunks_are_removed, test_per_document_cap_keeps_rank_order) |

## LLM: Generation and the model endpoint

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| LLM-01 | LLM endpoint times out | Request fails with a clear 504 after a bounded timeout, metric incremented, no hanging connection | mock with delay | DONE (test_llm01_server_that_never_answers_times_out_and_lets_go, test_llm01_stream_slower_than_the_total_budget_times_out, test_llm01_model_timeout_is_a_clear_504_and_counted). Bounds: 30 s silence, 60 s total per call |
| LLM-02 | Stream breaks halfway | Client receives an explicit error event; partial answers are never cached | mock disconnect | DONE (test_llm02_stream_broken_after_content_is_an_error_not_a_short_answer, test_llm02_stream_ending_without_finish_is_an_error, test_llm02_broken_stream_is_an_error_event_and_never_cached) |
| LLM-03 | LLM returns an empty answer | Treated as failure, one bounded retry, then a clear error | mock empty | DONE (test_llm03_empty_answer_is_retried_once_then_fails, test_llm03_empty_then_good_answer_succeeds, test_llm03_empty_answers_give_a_clear_error) |
| LLM-04 | LLM returns HTTP 429 or 503 | Bounded retries with backoff and jitter; circuit breaker opens after repeated failure | mock error codes | DONE (test_llm04_transient_503s_are_retried_with_backoff, test_llm04_retries_are_bounded, test_llm04_backoff_has_full_jitter_and_honours_retry_after, test_llm04_circuit_opens_and_fails_fast, test_llm04_half_open_lets_one_trial_through_and_closes_on_success, test_llm04_overloaded_model_gives_503_then_the_circuit_opens) |
| LLM-05 | Client disconnects mid-stream | Upstream generation cancelled; no leaked tasks or connections | disconnect test, check open connections | DONE (test_llm05_cancel_stops_the_stream_and_closes_the_connection, test_llm05_client_disconnect_cancels_the_generation, test_llm05_cancelled_run_stops_before_the_next_node). Limit: a cancel is noticed at the next streamed chunk, so a model still reading a long prompt finishes that first |
| LLM-06 | Prompt plus requested output exceeds max model length | Rejected or trimmed before sending, with a precise message | long-prompt test | DONE (test_llm06_oversized_prompt_is_rejected_before_sending, test_llm06_prompt_that_fits_is_sent; the agent already trims context, RET-06). Token count is an estimate (3 chars per token, conservative) |
| LLM-07 | Different servers give different token streams (format quirks) | Adapter normalises chunk format; tests run against mock and Ollama | contract test | DONE (test_llm07_every_streaming_flavour_gives_the_same_text, test_llm07_sse_parsing_tolerates_quirks, test_llm07_non_streaming_json_body_is_accepted, test_llm07_contract_with_real_ollama, test_llm07_quirky_server_format_still_answers_end_to_end) |
| LLM-08 | Model refuses or adds disclaimers | Passed through unchanged; not treated as an error | fixture | DONE (test_llm08_model_disclaimers_pass_through_unchanged, test_llm08_trailing_disclaimer_without_numbers_is_kept) |
| LLM-09 | Mock server is too perfect | Mock supports configurable latency, errors, slow-first-token and malformed chunks | mock feature test | DONE (services/mock_llm/tests/test_mock_llm.py: latency, slow first token, 429/503 with Retry-After, empty, malformed chunks, mid-stream disconnect, hang, 4 streaming flavours; test_llm09_malformed_chunks_are_skipped_and_counted) |

## API: Gateway behaviour

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| API-01 | Missing, malformed or wrong API key | 401 with no detail leaked; constant-time comparison on hashes | unit tests | DONE (test_api01_bad_keys_get_the_same_401, test_api01_right_id_wrong_secret_is_rejected_in_constant_time, test_api01_only_a_hash_is_stored_and_revocation_takes_effect, test_api01_info_endpoint_also_needs_a_key). ADR-025 |
| API-02 | Rate limit exceeded | 429 with `Retry-After` | burst test | DONE (test_api02_burst_over_the_limit_gets_429_with_retry_after, test_api02_limits_are_per_key) |
| API-03 | Empty, huge or non-UTF-8 question | 422 for invalid, hard size cap for huge | fuzz test | DONE (test_api03_invalid_bodies_get_422, test_api03_question_over_the_limit_gets_a_precise_422, test_api03_huge_body_hits_the_hard_cap, test_api03_random_bytes_never_cause_a_500) |
| API-04 | Concurrent identical requests (stampede) | One computation, others wait or are served from cache (request coalescing) | concurrency test | DONE (test_api04_identical_concurrent_requests_share_one_computation: 5 requests, 1 generation). Coalescing is per gateway process; across replicas the cache catches repeats |
| API-05 | Streaming through proxies buffers output | SSE headers set to disable buffering; documented | manual check | DONE (test_api05_sse_headers_disable_proxy_buffering_and_keepalives_flow; headers and keep-alives documented in sse.py and ADR-024). On kind 2026-10-07 (`scripts/kind_proxy_check.sh`): through nginx 1.30.5 with default buffering, the first progress event arrived after 0.05-0.15 s and the answer after 0.65-0.76 s, i.e. streamed; the same with `X-Accel-Buffering` ignored, so on this proxy the header was not what made it work |
| API-06 | Request ID missing | Generated, returned in the response, attached to logs and traces | test | DONE (test_api06_request_id_is_generated_returned_and_logged, test_api06_valid_incoming_id_is_kept_and_unsafe_one_replaced, test_api06_request_id_is_in_the_sse_meta_event). Traces arrive in Phase 3 |
| API-07 | Slow client (backpressure) | Server doesn't buffer unlimited data; stream cancelled after a timeout | slow-reader test | DONE (test_api07_client_that_stops_reading_is_cut_off: 15 s write timeout, then the run is released) |
| API-08 | Graceful shutdown during in-flight requests | In-flight requests finish within a grace period; new ones rejected | SIGTERM test | DONE (test_api08_in_flight_requests_finish_and_new_ones_are_refused; manual 2026-10-06: `docker compose stop gateway` during an 8 s request, the request finished with 200, exit code 0; test_api08_draining_reports_not_ready_but_still_serves_open_connections). Changed in Phase 4: requests arriving on open keep-alive connections while draining are served with `Connection: close` instead of 503 (new connections are refused by the closed listener) |

## CAC: Cache and rate limiter

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| CAC-01 | Redis is down | Cache skipped (fail open); rate limit uses a conservative in-memory fallback; `/readyz` reports degraded, not failed | stop Redis | DONE (test_cac01_redis_down_fails_open_and_reports_degraded, test_cac01_rate_limit_falls_back_to_a_stricter_local_limit, test_cac01_redis_recovers_without_a_restart; `make drill` against real Redis) |
| CAC-02 | Corpus, prompt or model version changes | Cache key changes, so stale answers are never served | version bump test | DONE (test_cac02_key_changes_with_corpus_prompt_and_model, test_cac02_reingest_means_no_stale_answer, test_cac02_prompt_version_tracks_the_prompt_text) |
| CAC-03 | Error or abstention answers | Errors never cached; abstentions cached only with a short TTL | test | DONE (test_cac03_ttl_policy, test_cac03_errors_are_never_cached, test_cac03_abstentions_get_the_short_ttl) |
| CAC-04 | Cache poisoning via crafted question | Key includes normalised question only; no user-controlled key parts beyond the hash | review plus test | DONE (test_cac04_crafted_questions_cannot_shape_the_key, test_cac04_normalisation_is_meaning_preserving_only; review: only the question, hashed, is user input) |
| CAC-05 | Cache on during benchmarks | Benchmark harness refuses to run with the cache enabled | guard test | DONE (test_cac05_benchmark_guard_refuses_a_cached_gateway; `bench/guard.py`, used by every load script from Phase 3) |
| CAC-06 | Clock skew or Redis restart resets counters | Documented; limiter errs on allowing slightly more, not locking everyone out | note | DONE (test_cac06_limiter_uses_redis_time_not_the_gateway_clock, test_cac06_redis_restart_refills_buckets_rather_than_locking_out; documented in redis_state.py and LEARNING.md) |

## DEP: Dependency failures

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| DEP-01 | Qdrant is down | `/readyz` fails; requests get 503 with a clear message; no hallucinated answers | stop Qdrant | DONE (test_dep01_qdrant_down_gives_503_and_never_calls_the_model, test_dep01_qdrant_failing_mid_request_is_also_503, test_dep01_missing_collection_says_to_ingest, test_dep01_recovery_needs_no_restart; `make drill` against real Qdrant) |
| DEP-02 | PostgreSQL is down | Requests still succeed; logs buffered (bounded) or dropped with a metric | stop Postgres | DONE (test_dep02_postgres_down_requests_still_succeed_and_logs_are_buffered, test_dep02_buffer_is_bounded_and_drops_are_counted; `make drill`: the request made during the outage was in the log after recovery) |
| DEP-03 | Langfuse unreachable or rate-limited | Requests unaffected; tracing exporter times out quickly and drops | block network to Langfuse | DONE (test_dep03_tracing_backend_failures_do_not_touch_requests: refused, 429 and hanging OTLP endpoints, request latency unchanged, shutdown bounded; test_dep03_langfuse_target_is_built_from_settings_without_logging_secrets). Export to real Langfuse Cloud not tested: no account was created |
| DEP-04 | Services start in the wrong order | Retries with backoff; readiness gates traffic; no crash loops | `docker compose up` cold start | DONE (test_dep04_starts_with_every_dependency_down_then_becomes_ready; manual 2026-10-06: `make down && make up-lite`, no depends_on, gateway ready in 9 s with 0 restarts). Kubernetes probes in Phase 4 |
| DEP-05 | Disk full on a volume | Clear error, alert metric, no corrupted data | note and manual test | DONE (drill 2026-10-07, docs/runbooks/disk-full.md: PostgreSQL on a full volume. Table file full: statement error, server up, data intact, recovers when space is freed. WAL full: PANIC and stop, crash recovery on restart once 16 MB is free. The gateway keeps serving and counts dropped log rows, alert FxaRequestLogDropping). Qdrant disk full not drilled |
| DEP-06 | Hugging Face download fails in notebook | Retry, resume partial downloads, fail with an actionable message | simulate offline | DONE (test_dep06_download_retries_resumes_and_fails_with_the_fix: 5 attempts with backoff, partial files resumed by snapshot_download, a final error that says what to check; only needed files fetched) |

## OBS: Observability

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| OBS-01 | High-cardinality labels (user ID, question text) | Forbidden as metric labels; lint check or test | label allowlist test | DONE (test_obs01_every_metric_attribute_is_on_the_allowlist: real traffic, every fxa_* attribute checked against metrics.ALLOWED_ATTRIBUTES, no keys, questions or request IDs; test_obs01_every_call_site_uses_literal_allowlisted_label_keys: AST check of every call site) |
| OBS-02 | Sensitive data in logs or traces | API keys and full prompts redacted or truncated; PII-safe by default | log scan test | DONE (test_obs02_logs_are_redacted, test_obs02_request_log_and_spans_carry_no_question_or_key_by_default, test_obs02_content_tracing_is_opt_in_truncated_and_redacted). Questions are stored as a hash and length only; content on spans needs FXA_TRACE_CONTENT=true |
| OBS-03 | Dashboard has no data | Panels show "no data" clearly, and every panel has a documented query | review | DONE (test_obs03_every_panel_has_a_description_query_and_no_data_text, test_obs03_dashboard_json_is_generated_from_the_spec, test_obs03_queries_and_alerts_only_use_metrics_that_exist; panel table with queries in observability/README.md and LEARNING.md). Manual 2026-10-07: every panel query returned data after `make load`, except counters that had never incremented, which show their No data text |
| OBS-04 | Metrics endpoint unauthenticated | Exposed only inside the compose network or cluster, not publicly | config review | DONE (test_obs04_api_port_does_not_serve_metrics, test_obs04_compose_never_publishes_the_metrics_port; metrics on port 9464 inside the compose network only). Kubernetes: ClusterIP only, Phase 4 |
| OBS-05 | Time to first token measured wrongly (includes queueing or retrieval) | Metrics separate retrieval, queue, TTFT and total time | test with mock delays | DONE (test_obs05_ttft_excludes_retrieval_and_queueing: mock first-token delay 0.4 s measured as TTFT 0.4-0.8 s while retrieval < 0.2 s; test_obs05_queue_time_is_its_own_stage) |

## K8S: Kubernetes and Helm (kind)

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| K8S-01 | Slow startup makes liveness kill the pod | Startup probe protects slow starts; documented values | start-delay test | DONE (test_k8s01_gateway_probes_protect_slow_starts; kind 2026-10-07: 60 s artificial startup delay, startup probe budget 180 s: 0 restarts; same delay with a 30 s budget: the new pod was restarted twice in 150 s while the old pods kept serving) |
| K8S-02 | Pod OOMKilled | Resource limits and requests set; alert query documented; runbook written | memory-hog test | DONE (test_k8s02_every_container_has_requests_and_limits; kind 2026-10-07: unplanned ingestion OOMKilled at 1.5 GiB, fixed with embedding batch 16 (peak ~931 MiB); drill: mock LLM allocated 200 MiB over a 128 Mi limit, OOMKilled (exit 137), restarted and ready in 8 s; docs/runbooks/pod-oomkilled.md with the alert queries). No kube-state-metrics on kind, so the alert queries are documented, not running |
| K8S-03 | Watchdog restart loop | Needs N consecutive failures plus a cooldown; max restarts per hour | failure-injection test | DONE (test_k8s03_cooldown_blocks_a_second_restart, test_k8s03_hourly_cap_then_hand_over_to_a_human, test_k8s03_cap_window_slides, test_k8s03_broken_model_is_restarted_exactly_once_per_policy; `make kind-watchdog-drill` 2026-10-07: warn, warn, restart; new pod broken again: warn, warn, suppressed; exactly one restart) |
| K8S-04 | Watchdog false positive (slow but healthy) | Latency threshold and canary prompt tuned; warning before action | test | DONE (test_k8s04_single_or_intermittent_failures_only_warn, test_k8s04_restart_needs_n_consecutive_failures_after_warnings, test_canary_judges_status_content_and_latency: slow under 20 s is healthy, slower is a failure; warnings logged before any action) |
| K8S-05 | Watchdog permissions too broad | RBAC limited to one named deployment | `kubectl auth can-i` checks | DONE (test_k8s05_watchdog_role_allows_one_deployment_only; `make kind-rbac-check`: 17 `kubectl auth can-i` cases, get/patch on the mock LLM Deployment only) |
| K8S-06 | Secrets in values files | Placeholders only; real values from local env or a Secret created by script | grep check in CI | DONE (test_k8s06_values_files_hold_no_secrets, test_k8s06_chart_creates_no_secret_and_reads_one_by_reference; scripts/kind-secrets.sh creates the Secret from .env). CI grep job in Phase 5 |
| K8S-07 | Rolling update drops requests | Readiness gates and PodDisruptionBudget verified during an update | rollout test | DONE (test_k8s07_rollouts_keep_capacity_and_drain; `make kind-rollout-test` 5 runs on 2026-10-07, the last 3 consecutive with 0 failed requests of 32-36). Found and fixed: draining answered keep-alive requests with 503, now served with `Connection: close` |
| K8S-08 | kind image not found | Documented `kind load docker-image` step in Makefile | fresh-clone test | DONE (test_k8s08_kind_never_pulls_app_images; `make kind-load` saves one platform per image and runs `kind load image-archive`, because `kind load docker-image` fails with Docker's containerd image store). Fresh-clone test in Phase 8 |
| K8S-09 | Helm values drift between environments | One base values file plus small overrides; `helm template` diff checked | CI | DONE (test_k8s09_environment_overrides_stay_small; `make helm-check` prints the rendered diff per environment: kind 3 overrides, ci 9). Runs in CI from Phase 5 |

## CI: Pipeline

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| CI-01 | Flaky tests (timing, ports) | No fixed sleeps; use readiness polling and random free ports | repeat 10 times | DONE (no fixed sleeps in tests: readiness polling and random free ports, `fxassist_mock_llm.server`; the full suite of about 360 tests ran 16 times on 2026-10-07 with 0 failures; one flaky test found earlier (API-08's late request reset by the closing listener) was fixed). Open, not a test failure: in about 30% of runs the pytest process takes ~60 s longer to exit after the last test. Ruled out: atexit callbacks (timed) and pytest hooks; the main thread sleeps during late interpreter shutdown, likely in a native extension. Not yet bisected by test file CI job timeouts allow for it |
| CI-02 | Pull request from a fork | No secrets required; all jobs pass | fork PR | TODO: partial. Done: no workflow references secrets, `pull_request` (not `pull_request_target`), read-only token (test_ci02_ci05_no_secrets_no_gpu_read_only). Left: the first real fork PR, after the repo is pushed to GitHub (its remote is a local folder today) |
| CI-03 | Dependency drift | Locked dependencies; a scheduled weekly job reports updates | lockfile check | DONE (`uv lock --check` in CI; weekly `.github/workflows/dependencies.yml` reports what `uv lock --upgrade` would change without changing it; run locally 2026-10-07: 2 packages had updates; test_ci03_lockfile_check_and_weekly_drift_report) |
| CI-04 | Docker build cache stale or huge images | Multi-stage builds; image size budget checked | CI size check | DONE (multi-stage images, `make image-budget`: gateway 236/270 MB, mock 53/60, watchdog 49/55 compressed; built and budgeted in CI, test_ci04_images_are_built_budgeted_and_scanned) |
| CI-05 | Anything in CI needs a GPU or paid API | Forbidden; CI is fully mockable | review | DONE (test_ci02_ci05_no_secrets_no_gpu_read_only, test_ci05_nothing_calls_a_real_model_or_paid_api: standard runners, mock LLM everywhere, the Ollama contract test skips when CI=true) |

## GPU: Kaggle lab

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| GPU-01 | vLLM version doesn't support T4 | 10 minute smoke test first; pin a compatible version; record in `docs/VERSIONS.md` | smoke test cell | DONE (harness): vLLM 0.31.0 checked against its docs and installed source for compute capability 7.5 (docs/VERSIONS.md); `lab.smoke_test` runs first with a 10-minute cap, tries the default backend then TRITON_ATTN, records the backend and that ignore_eos works (test_lab_dry_run_in_mock_mode). First real run: PENDING (Phase 6) |
| GPU-02 | Model defaults to bfloat16 | Set float16 explicitly; startup fails loudly if the dtype is unsupported | config check | DONE (test_gpu02_every_server_runs_float16_explicitly: `--dtype float16` on every server; the FP16 model's config says bfloat16 and `--dtype auto` would pick it; test_gpu09_gpu10_problems_are_reported_with_the_fix: bf16_supported false for compute capability 7.5) |
| GPU-03 | Out-of-memory on load or under load | Documented memory formula; knob table (memory utilization, max length, max sequences); runbook | induced OOM drill | TODO |
| GPU-04 | Library conflicts with preinstalled packages | Isolated virtual environment; pinned versions | clean-notebook run | DONE (`lab.make_venv`: vLLM and the eval each in a fresh virtualenv under scratch, pinned versions) |
| GPU-05 | Session killed or times out | Every result row is written to disk immediately; run is resumable from a checkpoint file | kill mid-run | DONE (test_gpu05_rows_are_on_disk_at_once_and_runs_resume: rows fsync'd per repetition, a cut-off last row ignored and redone; stages.jsonl per stage; test_lab_dry_run_in_mock_mode re-runs and skips finished work) |
| GPU-06 | Weekly GPU hours exhausted | Session plan with hour budget; priority order of runs; cut stretch goals first | plan in `docs/KAGGLE_PLAYBOOK.md` | DONE (session plan with hour budget and priority order in docs/KAGGLE_PLAYBOOK.md; test_gpu06_hour_budget_stops_new_experiments; MAX_PRIORITY cuts groups) |
| GPU-07 | Only one GPU assigned instead of two | Multi-GPU run skipped with a documented note; nothing crashes | check device count | DONE (test_gpu07_fewer_than_two_gpus_skips_the_multi_gpu_run, test_gpu07_tensor_parallel_is_skipped_with_one_gpu: recorded as skipped with the GPU count) |
| GPU-08 | NCCL or peer-to-peer problems on 2 GPUs | Record the exact error, try documented environment workarounds once, then stop and write it up | tensor-parallel attempt | TODO |
| GPU-09 | Internet disabled in the notebook | Setup cell detects and tells me to enable it (and verify the account if required) | check cell | DONE (test_gpu09_gpu10_problems_are_reported_with_the_fix: no internet stops with the Settings > Internet and phone-verification fix) |
| GPU-10 | Disk quota exceeded by model files | Download only needed files; clean caches; check free space first | disk check cell | DONE (environment check measures free scratch space against ~17 GB needed; downloads only *.json, *.safetensors and tokenizer files; weights live in /kaggle/tmp, only small results in /kaggle/working) |
| GPU-11 | Notebook outputs leak tokens (Hugging Face) | Tokens via notebook secrets only; never printed; output cells reviewed before publishing | review | DONE (token read from Kaggle/Colab secrets into the environment only, never printed; test_notebook_is_generated_and_only_calls_tested_code checks no print of it and that the committed notebook has no outputs; playbook says to review outputs before sharing) |
| GPU-12 | Colab used as fallback behaves differently | Notebook parametrised; differences noted | optional | DONE (the notebook detects Colab: /content paths, google.colab.userdata secrets, files.download). Not run on Colab |

## BEN: Benchmark validity

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| BEN-01 | Cold start skews results | Warm-up requests discarded | harness check | DONE (harness: warm-up requests per repetition, discarded; test_ben01_ben08_cell_row_records_fixed_output_and_discards_warmup) |
| BEN-02 | Cache or prefix caching inflates numbers | Cache off; prefix caching reported as an explicit variable | config recorded in every result row | DONE (harness bypasses the gateway and its cache; vLLM prefix caching off except in its own experiment, recorded in every row; test_ben01_ben08_cell_row_records_fixed_output_and_discards_warmup) |
| BEN-03 | Single run noise | At least 3 repetitions; report median and spread | harness | DONE (3 repetitions per cell, median and min-max in the report; test_ben03_every_cell_has_at_least_three_repetitions) |
| BEN-04 | Prompt set differs between runs | Fixed prompt file with hash recorded | hash in results | DONE (prompt sets built from repo files, SHA-256 in every row; test_ben04_prompt_sets_are_fixed_and_hashed) |
| BEN-05 | Averages hide tail latency | Report p50 and p95 | harness | DONE (p50 and p95, nearest rank, None instead of zero for no data; test_ben05_percentiles_are_nearest_rank_and_never_invented) |
| BEN-06 | Errors counted as fast successes | Error rate reported; failed requests excluded from latency but shown separately | harness | DONE (errors counted by type and left out of latency; test_ben06_errors_are_counted_and_kept_out_of_latency) |
| BEN-07 | Several knobs changed at once | One variable per experiment; experiment log table | review | DONE (`check_one_variable` refuses an experiment that changes more than one server setting; test_ben07_each_knob_experiment_changes_one_setting) |
| BEN-08 | Output length varies, so tokens per second misleads | Fix max output tokens; report generated token counts | harness | DONE (max_tokens 128 plus vLLM's ignore_eos, token counts from usage; smoke test checks exact length; test_ben01_ben08_cell_row_records_fixed_output_and_discards_warmup) |
| BEN-09 | Unfair comparison FP16 vs AWQ | Same model, same prompts, same settings, separate runs | review | DONE (test_ben09_fp16_and_awq_differ_only_in_the_model: same cells and settings, separate servers) |

## SAF: Safety, security and domain

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| SAF-01 | User asks "should I buy EUR/USD now?" | Declines personalised trading or investment advice; offers general documented information only | fixture | DONE (test_saf01_advice_is_declined_by_code_and_risks_are_summarised; eval s01-s04) |
| SAF-02 | User asks for guaranteed returns or profit tips | Declines; cites risk warnings from the documents if available | fixture | DONE (test_guards_flag_unsafe_or_vague_questions; eval s02) |
| SAF-03 | Answer states a number (leverage limit, fee) not in the sources | Numbers must be supported by a cited chunk; otherwise abstain | eval check | DONE (test_saf03_invented_number_is_rejected, test_saf03_list_numbering_is_not_treated_as_a_claimed_number; eval scenario c03). Limitation: a number that exists in the cited excerpt but is attributed wrongly (eval a02: 20:1 for majors) passes |
| SAF-04 | Prompt injection in user input | System instructions hold; test set of 10 attacks | adversarial set | DONE (eval/attacks.yaml 10/10 in the final local run; test_guards_flag_unsafe_or_vague_questions, test_saf04_citation_after_full_stop_does_not_cover_the_next_sentence) |
| SAF-05 | Request to reveal system prompt or keys | Refused | fixture | DONE (test_saf04_saf05_refusals_never_reach_the_model; eval r01-r02) |
| SAF-06 | Every answer must be framed as informational | Standard disclaimer added by the gateway, not by the model | test | DONE (test_saf06_disclaimer_is_on_every_kind_of_answer, test_json_answer_has_citations_and_the_gateway_disclaimer) |
| SAF-07 | Secrets committed to git | Pre-commit secret scan and CI scan | scan job | DONE (pre-commit gitleaks: test_saf07_precommit_runs_gitleaks; CI full-history scan: test_saf07_ci_scans_full_history; manual: planted token caught). First real CI run pending a push to GitHub |
| SAF-08 | Container runs as root or image has known vulnerabilities | Non-root user; vulnerability scan with a free scanner reported, not blocking | CI | DONE (compose services and our own gateway and mock LLM images run non-root with all capabilities dropped (test_saf08_runs_as_non_root, test_saf08_ollama_dockerfile_drops_root, test_saf08_own_images_switch_to_a_non_root_user); Trivy v0.75.0 in CI, report only: the first local scan found 2 HIGH CVEs in the base image's setuptools/wheel, fixed by removing pip, setuptools and wheel from the runtime stage, 0 after, 2026-10-07) |

## ENV: Developer environment

| ID | Scenario | Expected | Test | Status |
|---|---|---|---|---|
| ENV-01 | Windows line endings break shell scripts | `.gitattributes` forces LF for scripts | fresh-clone test | DONE (test_env01_gitattributes_forces_lf, test_env01_no_crlf_in_tracked_text_files; bootstrap also checks the checkout) |
| ENV-02 | Docker Desktop memory too low for the full stack | Documented minimum; a "lite" compose profile for lower-RAM machines | check | DONE (bootstrap memory check (test_env02_docker_memory_tiers), minimums in README, test_env02_memory_limit_set, test_env02_lite_profile_fits_budget. `full` profile (Prometheus, Grafana) measured 2026-10-07: whole compose stack without Ollama used about 825 MiB (sum of docker stats), limits total 2.6 GiB; kind node measured 2026-10-07: 2.1 GiB in total, 803 MiB of our pods) |
| ENV-03 | Ollama not running or model not pulled | Clear startup error with the exact fix command | test | DONE (test_env03_model_not_pulled_gives_the_exact_fix, test_env03_server_down_gives_the_exact_fix; bootstrap 'Local model' section) |
| ENV-04 | Port conflicts on the host | Ports configurable by environment variables | test | DONE (test_env04_ports_come_from_env, test_env04_every_variable_is_documented; manual 2026-10-06: host Postgres held 5432, FXA_POSTGRES_PORT=55432 worked) |
| ENV-05 | Fresh clone doesn't work | `make bootstrap` followed by `make demo` works on a clean machine; verified in CI where possible | clean clone test | TODO: partial. Done: `make bootstrap` (tests/test_bootstrap.py, CI step on a clean runner). `make demo` works (Phase 2, run 2026-10-06 with LLM=ollama). Left: fresh-clone test in Phase 8 |
