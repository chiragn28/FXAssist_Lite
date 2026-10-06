"""OpenTelemetry setup for the gateway process (ADR-012, Phase 3).

Metrics: the OTel SDK with a Prometheus exporter, served on its own port (`FXA_METRICS_PORT`,
9464) and not on the API port. Compose does not publish it to the host and Kubernetes exposes it
only inside the cluster, so metrics are reachable by Prometheus but not by API clients (OBS-04).

Traces: spans for each request, each agent graph node and each LLM call. They are exported only
when an OTLP endpoint is configured: Langfuse Cloud (`FXA_LANGFUSE_PUBLIC_KEY` and
`FXA_LANGFUSE_SECRET_KEY`) or any OTLP/HTTP endpoint (`FXA_OTLP_TRACES_ENDPOINT`).
DEP-03: tracing must never slow or fail a request. Spans are queued in memory (at most 512) and
sent by a background thread with a 2 s timeout; when the queue is full, new spans are dropped.
OBS-02: question and answer text are not put on spans unless `FXA_TRACE_CONTENT=true`, and even
then they are truncated and API keys are redacted.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import Histogram, MeterProvider
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from fxassist_agent.tracing import redact

from . import metrics as fxa_metrics
from .config import GatewaySettings

log = logging.getLogger(__name__)

# --- Redaction (OBS-02) --------------------------------------------------------------------


class RedactingFilter(logging.Filter):
    """Removes API keys, bearer/basic credentials and passwords from every log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


# --- Setup -----------------------------------------------------------------------------------


@dataclass
class TraceTarget:
    endpoint: str
    headers: dict[str, str]
    name: str  # for log messages only; never includes secrets


def trace_target(settings: GatewaySettings) -> TraceTarget | None:
    if settings.otlp_traces_endpoint:
        headers = dict(
            part.split("=", 1)
            for part in settings.otlp_headers.get_secret_value().split(",")
            if "=" in part
        )
        return TraceTarget(settings.otlp_traces_endpoint, headers, "OTLP endpoint")
    public = settings.langfuse_public_key
    secret = settings.langfuse_secret_key.get_secret_value()
    if public and secret:
        auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
        return TraceTarget(
            settings.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces",
            {"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
            f"Langfuse at {settings.langfuse_host}",
        )
    return None


def histogram_views() -> list[View]:
    return [
        View(
            instrument_type=Histogram,
            instrument_name=name,
            aggregation=ExplicitBucketHistogramAggregation(buckets),
        )
        for name, buckets in [
            ("fxa_request_duration", fxa_metrics.SECONDS_BUCKETS),
            ("fxa_stage_duration", fxa_metrics.SECONDS_BUCKETS),
            ("fxa_llm_tokens_per_second", fxa_metrics.TOKENS_PER_SECOND_BUCKETS),
        ]
    ]


def setup_telemetry(settings: GatewaySettings) -> TracerProvider:
    """Install the metric and trace providers for this process. Call once, before the app."""
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from prometheus_client import start_http_server

    resource = Resource.create({"service.name": "fxassist-gateway", "service.version": "0.3.0"})
    reader = PrometheusMetricReader()
    metrics.set_meter_provider(
        MeterProvider(metric_readers=[reader], resource=resource, views=histogram_views())
    )
    start_http_server(settings.metrics_port, addr=settings.metrics_host)
    log.info("metrics on %s:%d/metrics", settings.metrics_host, settings.metrics_port)

    provider = TracerProvider(resource=resource)
    target = trace_target(settings)
    if target is not None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(
            endpoint=target.endpoint,
            headers=target.headers,
            timeout=settings.trace_export_timeout_s,
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=512,
                max_export_batch_size=128,
                schedule_delay_millis=2000,
                export_timeout_millis=settings.trace_export_timeout_s * 1000,
            )
        )
        log.info("exporting traces to %s", target.name)
    else:
        log.info("trace export off (set FXA_LANGFUSE_* or FXA_OTLP_TRACES_ENDPOINT)")
    trace.set_tracer_provider(provider)
    return provider


def register_gauges(components) -> None:
    """Point-in-time values read when Prometheus scrapes."""
    from opentelemetry.metrics import CallbackOptions, Observation

    meter = fxa_metrics.meter
    states = {"closed": 0, "half_open": 1, "open": 2}

    def breaker(_: CallbackOptions):
        yield Observation(states[components.llm.breaker.state])

    def active(_: CallbackOptions):
        yield Observation(components.runner.active)

    def log_queue(_: CallbackOptions):
        yield Observation(components.logs.queue.qsize())

    def opened(_: CallbackOptions):
        yield Observation(components.llm.breaker.opened_count)

    meter.create_observable_gauge(
        "fxa_llm_circuit_state", [breaker], description="0 closed, 1 half-open, 2 open"
    )
    meter.create_observable_counter(
        "fxa_llm_circuit_opened", [opened], description="Times the LLM circuit breaker opened"
    )
    meter.create_observable_gauge(
        "fxa_agent_runs_active", [active], description="Agent runs in worker threads"
    )
    meter.create_observable_gauge(
        "fxa_request_log_queue", [log_queue], description="Request log entries waiting"
    )
