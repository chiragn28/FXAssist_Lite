"""Metric instruments, created through the OpenTelemetry API (ADR-012).

Without a configured MeterProvider these are no-ops (tests and the CLI pay nothing).
`telemetry.setup_telemetry` installs the SDK with a Prometheus exporter; Prometheus names get
`_total` on counters and `_seconds` on second-based histograms, e.g. `fxa_requests_total`.

OBS-01: attribute (label) values must come from small fixed sets. ALLOWED_ATTRIBUTES lists every
attribute key in use, and a test checks real traffic against it. Never user IDs, API keys,
questions or request IDs: those go to logs and traces, which are made for them.

OBS-05: time is split into stages that are measured separately:
  queue      waiting for a free agent slot (gateway)
  retrieval  embedding the question and searching Qdrant (agent `retrieve` node)
  ttft       from sending the generation request to its first content token (LLM adapter)
  llm        total time inside LLM calls, including the grader
  request    the whole request, as the client sees it up to the answer
"""

from __future__ import annotations

from opentelemetry import metrics

meter = metrics.get_meter("fxassist.gateway")

# Attribute keys and the values they may take (None = a small set defined in code, e.g. codes).
ALLOWED_ATTRIBUTES: dict[str, set[str] | None] = {
    "route": {"/v1/ask"},
    "status": None,  # HTTP status codes
    "outcome": {
        "answered",
        "abstained",
        "out_of_scope",
        "clarify",
        "declined_advice",
        "refused",
        "error",
        "none",
    },
    "code": None,  # error codes from errors.py and fxassist_agent.llm
    "cached": {"true", "false"},
    "kind": {"text", "json"},
    "result": None,  # ok, an error code, or a cache result (hit/miss/skip/disabled)
    "backend": {"redis", "memory"},
    "allowed": {"true", "false"},
    "dependency": {"redis", "postgres", "qdrant"},
    "stage": {"queue", "retrieval", "ttft", "llm"},
}

SECONDS_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120]
TOKENS_PER_SECOND_BUCKETS = [1, 2, 5, 10, 20, 30, 40, 60, 80, 100, 150, 200, 300]

# --- Counters ------------------------------------------------------------------------------
requests = meter.create_counter(
    "fxa_requests", unit="{request}", description="Requests by route, status and outcome"
)
errors = meter.create_counter("fxa_errors", unit="{error}", description="Errors by code")
llm_calls = meter.create_counter(
    "fxa_llm_calls", unit="{call}", description="LLM calls by kind and result"
)
llm_output_tokens = meter.create_counter(
    "fxa_llm_output_tokens", unit="{token}", description="Tokens generated, as the server reports"
)
llm_malformed_chunks = meter.create_counter(
    "fxa_llm_malformed_chunks", unit="{chunk}", description="Unparseable stream chunks skipped"
)
cache_lookups = meter.create_counter(
    "fxa_cache_lookups", unit="{lookup}", description="Cache lookups by result: hit, miss, skip"
)
coalesced = meter.create_counter(
    "fxa_coalesced_requests", unit="{request}", description="Requests served by another's run"
)
rate_limit = meter.create_counter(
    "fxa_rate_limit_decisions", unit="{decision}", description="Rate limit decisions by backend"
)
dependency_failures = meter.create_counter(
    "fxa_dependency_failures", unit="{failure}", description="Redis/Postgres/Qdrant failures"
)
log_entries_dropped = meter.create_counter(
    "fxa_request_log_dropped", unit="{entry}", description="Request log entries dropped (DEP-02)"
)
truncations = meter.create_counter(
    "fxa_context_truncations",
    unit="{excerpt}",
    description="Excerpts dropped or cut to fit the context window (RET-06)",
)
slow_clients = meter.create_counter(
    "fxa_slow_client_aborts", unit="{stream}", description="Streams aborted on write timeout"
)
cancelled_runs = meter.create_counter(
    "fxa_cancelled_runs", unit="{run}", description="Agent runs cancelled by client disconnect"
)

# --- Histograms (OBS-05) ---------------------------------------------------------------------
request_duration = meter.create_histogram(
    "fxa_request_duration", unit="s", description="Whole request, by outcome and cache use"
)
stage_duration = meter.create_histogram(
    "fxa_stage_duration",
    unit="s",
    description="Time per stage: queue, retrieval, ttft (first token), llm (all model calls)",
)
llm_tokens_per_second = meter.create_histogram(
    "fxa_llm_tokens_per_second",
    unit="{token}/s",
    description="Generation speed of answer calls: output tokens / (total - time to first token)",
)
