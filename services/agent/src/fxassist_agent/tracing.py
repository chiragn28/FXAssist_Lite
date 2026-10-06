"""Tracing helpers shared by the agent and the gateway (ADR-012).

Spans are created through the OpenTelemetry API: without an SDK (CLI, tests) they cost almost
nothing and go nowhere. The gateway installs the SDK and decides where spans are exported.

OBS-02: secrets never go on spans or log lines, and prompt/answer text only when the operator
switches content tracing on, truncated.
"""

from __future__ import annotations

import re

from opentelemetry import trace

tracer = trace.get_tracer("fxassist.agent")

_SECRETS = [
    (re.compile(r"fxa_[0-9a-f]{8}_[A-Za-z0-9_-]{20,}"), "fxa_<redacted>"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1<redacted>"),
    (re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=]{8,}"), r"\1<redacted>"),
    (re.compile(r"(sk|pk)-lf-[A-Za-z0-9-]{8,}"), r"\1-lf-<redacted>"),
    (re.compile(r"(?i)(password=)\S+"), r"\1<redacted>"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def content_for_span(text: str, limit: int = 1000) -> str:
    """What may go on a span when content tracing is switched on: redacted and truncated."""
    text = redact(text)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"
