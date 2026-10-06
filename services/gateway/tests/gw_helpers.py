"""Helpers the gateway tests import (kept out of conftest.py, whose module name is shared
with the agent's tests)."""

from __future__ import annotations

import json

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fxassist_gateway.telemetry import histogram_views

# One MeterProvider and one TracerProvider for the whole session (globals can be set once).
METRICS = InMemoryMetricReader()
otel_metrics.set_meter_provider(MeterProvider(metric_readers=[METRICS], views=histogram_views()))


class FanOutProcessor(SpanProcessor):
    """Lets a test attach an extra span processor (e.g. a broken exporter) and remove it."""

    def __init__(self) -> None:
        self.processors: list[SpanProcessor] = []

    def on_start(self, span, parent_context=None) -> None:
        for p in list(self.processors):
            p.on_start(span, parent_context)

    def on_end(self, span) -> None:
        for p in list(self.processors):
            p.on_end(span)

    def shutdown(self) -> None:
        for p in list(self.processors):
            p.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return all(p.force_flush(timeout_millis) for p in list(self.processors))


SPANS = InMemorySpanExporter()
EXTRA_SPAN_PROCESSORS = FanOutProcessor()
_tracer_provider = TracerProvider()
_tracer_provider.add_span_processor(SimpleSpanProcessor(SPANS))
_tracer_provider.add_span_processor(EXTRA_SPAN_PROCESSORS)
otel_trace.set_tracer_provider(_tracer_provider)


def metric_points():
    """Every (metric name, attributes, point) recorded so far."""
    data = METRICS.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for point in metric.data.data_points:
                    yield metric.name, dict(point.attributes), point


def histogram_points(name: str, **attributes: str) -> list:
    return [
        p
        for n, attrs, p in metric_points()
        if n == name and all(attrs.get(k) == v for k, v in attributes.items())
    ]


def metric_total(name: str, **attributes: str) -> float:
    """Sum of a counter's data points whose attributes include `attributes`."""
    total = 0.0
    data = METRICS.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    if all(point.attributes.get(k) == v for k, v in attributes.items()):
                        total += point.value
    return total


LEVERAGE = "Leverage limits for retail clients range from 30:1 for major currency pairs to 2:1 for crypto-assets."
MARGIN = "A margin call is a demand for more funds when the account falls below the required margin level."
PIP = "A pip is one unit of the fourth decimal place in a currency pair quote."


def parse_sse(text: str) -> list[tuple[str, dict | str]]:
    """[(event name, data)], with ':' comments returned as ('comment', text)."""
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name, data = "message", None
        for line in block.split("\n"):
            if line.startswith(":"):
                events.append(("comment", line[1:].strip()))
            elif line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if data is not None:
            events.append((name, data))
    return events
