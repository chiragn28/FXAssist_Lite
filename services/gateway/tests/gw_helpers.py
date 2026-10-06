"""Helpers the gateway tests import (kept out of conftest.py, whose module name is shared
with the agent's tests)."""

from __future__ import annotations

import json

from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

# One MeterProvider for the whole session (the global can only be set once).
METRICS = InMemoryMetricReader()
otel_metrics.set_meter_provider(MeterProvider(metric_readers=[METRICS]))


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
