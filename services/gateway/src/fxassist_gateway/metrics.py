"""Metric instruments, created through the OpenTelemetry API.

Until a MeterProvider is configured, these are no-ops, so tests and the CLI pay nothing.
Phase 3 configures the SDK with a Prometheus exporter; the call sites do not change.

OBS-01: attribute (label) values must come from small fixed sets: route templates, status
codes, outcome and error codes. Never user IDs, API keys, questions or request IDs.
"""

from __future__ import annotations

from opentelemetry import metrics

meter = metrics.get_meter("fxassist.gateway")

requests = meter.create_counter(
    "fxa_requests", unit="{request}", description="Requests by route, status and outcome"
)
errors = meter.create_counter("fxa_errors", unit="{error}", description="Errors by code")
llm_calls = meter.create_counter(
    "fxa_llm_calls", unit="{call}", description="LLM calls by kind and result"
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
    "fxa_context_truncations", unit="{excerpt}", description="Excerpts dropped to fit context"
)
slow_clients = meter.create_counter(
    "fxa_slow_client_aborts", unit="{stream}", description="Streams aborted on write timeout"
)
cancelled_runs = meter.create_counter(
    "fxa_cancelled_runs", unit="{run}", description="Agent runs cancelled by client disconnect"
)
