#!/usr/bin/env python3
"""Build the Grafana dashboard JSON and its panel documentation from one spec (OBS-03).

Every panel has: a PromQL query, a plain-language description (shown as the panel's (i) tooltip
in Grafana) and an explicit "no data" text that says what an empty panel means.

    python observability/grafana/build_dashboard.py            # writes dashboards/fxassist.json
    python observability/grafana/build_dashboard.py --markdown # prints the panel table
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "dashboards" / "fxassist.json"
DATASOURCE = {"type": "prometheus", "uid": "prometheus"}
NO_TRAFFIC = "No data: no requests in this time range. Send some with `make load`."


@dataclass
class Target:
    expr: str
    legend: str


@dataclass
class Panel:
    title: str
    description: str
    targets: list[Target]
    unit: str = "short"
    kind: str = "timeseries"  # or "stat"
    no_value: str = NO_TRAFFIC
    width: int = 12
    thresholds: list[tuple[float, str]] = field(default_factory=list)


def q(expr: str, legend: str = "") -> Target:
    return Target(" ".join(expr.split()), legend)


ROWS: list[tuple[str, list[Panel]]] = [
    (
        "Traffic and outcomes",
        [
            Panel(
                "Requests per second by outcome",
                "How many /v1/ask requests finish each second, split by what happened: answered, "
                "abstained (not enough information), out_of_scope, clarify, declined_advice, "
                "refused, error, or none (rejected before the agent ran, e.g. 401/429/422).",
                [
                    q(
                        'sum by (outcome) (rate(fxa_requests_total{route="/v1/ask"}[1m]))',
                        "{{outcome}}",
                    )
                ],
                unit="reqps",
            ),
            Panel(
                "Errors per second by code",
                "Failures by error code: llm_timeout, llm_unavailable, llm_circuit_open, "
                "store_unavailable, rate_limited, unauthorized and so on. Each code maps to one "
                "HTTP status (services/gateway/README.md).",
                [q("sum by (code) (rate(fxa_errors_total[1m]))", "{{code}}")],
                unit="reqps",
                no_value="No data: no errors in this time range.",
            ),
            Panel(
                "Fallback rate",
                "Share of requests the agent did not answer from the documents: abstained "
                "(nothing relevant enough) plus out_of_scope. A sudden rise usually means "
                "retrieval broke (empty or wrong collection) rather than users asking odd things.",
                [
                    q(
                        'sum(rate(fxa_requests_total{outcome=~"abstained|out_of_scope"}[5m])) '
                        '/ sum(rate(fxa_requests_total{outcome!="none"}[5m]))',
                        "fallback",
                    )
                ],
                unit="percentunit",
                kind="stat",
                width=6,
                thresholds=[(0, "green"), (0.3, "orange"), (0.6, "red")],
            ),
            Panel(
                "Cache hit ratio",
                "Share of cache lookups that found an answer. Lookups skipped because Redis is "
                "down are counted in the denominator, so an outage shows up as a drop here.",
                [
                    q(
                        'sum(rate(fxa_cache_lookups_total{result="hit"}[5m])) '
                        '/ sum(rate(fxa_cache_lookups_total{result=~"hit|miss|skip"}[5m]))',
                        "hit ratio",
                    )
                ],
                unit="percentunit",
                kind="stat",
                width=6,
                no_value="No data: no cache lookups (no traffic, or the cache is disabled).",
            ),
        ],
    ),
    (
        "Latency (OBS-05: each stage measured separately)",
        [
            Panel(
                "Request latency p50 / p95 (fresh answers)",
                "Whole request time for answers computed now (cache misses), from the gateway "
                "receiving the request to the answer being ready. Cached answers are excluded: "
                "they take milliseconds and would hide the real latency.",
                [
                    q(
                        "histogram_quantile(0.5, sum by (le) (rate("
                        'fxa_request_duration_seconds_bucket{cached="false",outcome="answered"}[5m])))',
                        "p50",
                    ),
                    q(
                        "histogram_quantile(0.95, sum by (le) (rate("
                        'fxa_request_duration_seconds_bucket{cached="false",outcome="answered"}[5m])))',
                        "p95",
                    ),
                ],
                unit="s",
            ),
            Panel(
                "Stage latency p95: queue, retrieval, time to first token, LLM total",
                "Where the time goes. queue: waiting for a free agent slot. retrieval: embedding "
                "the question and searching Qdrant. ttft: from sending the answer request to the "
                "model's first token (excludes queue and retrieval). llm: all model calls of the "
                "request together, including the grader.",
                [
                    q(
                        "histogram_quantile(0.95, sum by (le, stage) "
                        "(rate(fxa_stage_duration_seconds_bucket[5m])))",
                        "{{stage}}",
                    )
                ],
                unit="s",
            ),
            Panel(
                "Generation speed (tokens per second, p50)",
                "Decode speed of answer generation: tokens after the first, divided by the time "
                "after the first token. Needs a model server that reports token usage (Ollama "
                "and vLLM do; the mock does in its 'ollama' format).",
                [
                    q(
                        "histogram_quantile(0.5, sum by (le) "
                        "(rate(fxa_llm_tokens_per_second_bucket[5m])))",
                        "p50",
                    )
                ],
                unit="short",
                no_value="No data: no answers generated, or the model server does not report token usage.",
            ),
            Panel(
                "Tokens generated per second (all calls)",
                "Total model output rate across all requests, by call kind: text (answers, "
                "rewrites) and json (grader).",
                [q("sum by (kind) (rate(fxa_llm_output_tokens_total[1m]))", "{{kind}}")],
                unit="short",
            ),
        ],
    ),
    (
        "Model and reliability",
        [
            Panel(
                "LLM calls by result",
                "Model calls per second by kind and result: ok, or the error code (llm_timeout, "
                "llm_unavailable, llm_empty, llm_bad_response, cancelled...).",
                [q("sum by (kind, result) (rate(fxa_llm_calls_total[1m]))", "{{kind}} {{result}}")],
                unit="reqps",
            ),
            Panel(
                "Model call failure ratio",
                "Share of model calls that did not succeed: timeouts, server errors, broken "
                "streams, or refused by the open circuit breaker. This is the signal that caught "
                "a dead model in the drill; request error rates did not, because the cache and "
                "the breaker hide it (docs/runbooks/llm-timeout-storm.md).",
                [
                    q(
                        'sum(rate(fxa_llm_calls_total{result!="ok"}[2m])) '
                        "/ sum(rate(fxa_llm_calls_total[2m]))",
                        "failure ratio",
                    )
                ],
                unit="percentunit",
                no_value="No data: no model calls in this time range.",
            ),
            Panel(
                "Circuit breaker state",
                "0 = closed (normal), 1 = half-open (one trial call allowed), 2 = open (model "
                "calls paused after repeated failures; requests get 503 quickly).",
                [q("max(fxa_llm_circuit_state)", "state")],
                kind="stat",
                width=6,
                no_value="No data: the gateway is not being scraped (check the Prometheus targets page).",
                thresholds=[(0, "green"), (1, "orange"), (2, "red")],
            ),
            Panel(
                "Agent runs in progress",
                "Agent runs executing in worker threads right now (at most FXA_MAX_CONCURRENT_RUNS). "
                "At the limit, new requests queue; past the queue timeout they get 503 busy.",
                [q("sum(fxa_agent_runs_active)", "active")],
                kind="stat",
                width=6,
                no_value="No data: the gateway is not being scraped (check the Prometheus targets page).",
            ),
            Panel(
                "Context truncations and coalesced requests",
                "truncations: excerpts dropped or cut to fit the model's context window (RET-06). "
                "coalesced: requests that waited for an identical request's answer instead of "
                "starting their own run (API-04).",
                [
                    q("sum(rate(fxa_context_truncations_total[5m]))", "truncations/s"),
                    q("sum(rate(fxa_coalesced_requests_total[5m]))", "coalesced/s"),
                ],
                unit="short",
                no_value="No data: nothing truncated or coalesced in this time range.",
            ),
            Panel(
                "Dependency failures and degraded modes",
                "Failures talking to Redis, PostgreSQL or Qdrant; rate-limit decisions made by the "
                "in-memory fallback (Redis down); request-log entries dropped (PostgreSQL down too "
                "long). All should be zero in normal operation.",
                [
                    q(
                        "sum by (dependency) (rate(fxa_dependency_failures_total[5m]))",
                        "{{dependency}} failures/s",
                    ),
                    q(
                        'sum(rate(fxa_rate_limit_decisions_total{backend="memory"}[5m]))',
                        "local rate limit/s",
                    ),
                    q("sum(rate(fxa_request_log_dropped_total[5m]))", "log rows dropped/s"),
                ],
                unit="short",
                no_value="No data: no dependency failures in this time range (good).",
            ),
        ],
    ),
]


def build() -> dict:
    panels: list[dict] = []
    y = 0
    next_id = 1
    for row_title, row_panels in ROWS:
        panels.append(
            {
                "type": "row",
                "title": row_title,
                "id": next_id,
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
                "panels": [],
            }
        )
        next_id += 1
        y += 1
        x = 0
        for p in row_panels:
            if x + p.width > 24:
                x, y = 0, y + 8
            steps = [
                {"color": c, "value": None if i == 0 else v}
                for i, (v, c) in enumerate(p.thresholds)
            ]
            panels.append(
                {
                    "type": p.kind,
                    "title": p.title,
                    "description": p.description,
                    "id": next_id,
                    "datasource": DATASOURCE,
                    "gridPos": {"h": 8, "w": p.width, "x": x, "y": y},
                    "targets": [
                        {
                            "refId": chr(65 + i),
                            "datasource": DATASOURCE,
                            "expr": t.expr,
                            "legendFormat": t.legend,
                        }
                        for i, t in enumerate(p.targets)
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "unit": p.unit,
                            "noValue": p.no_value,
                            **(
                                {"thresholds": {"mode": "absolute", "steps": steps}}
                                if steps
                                else {}
                            ),
                        },
                        "overrides": [],
                    },
                    "options": {"legend": {"displayMode": "list", "placement": "bottom"}}
                    if p.kind == "timeseries"
                    else {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value"},
                }
            )
            next_id += 1
            x += p.width
        y += 8
    return {
        "uid": "fxassist-gateway",
        "title": "FXAssist gateway",
        "description": "Generated by observability/grafana/build_dashboard.py; edit the spec, not this file.",
        "tags": ["fxassist"],
        "timezone": "browser",
        "schemaVersion": 41,
        "version": 1,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "panels": panels,
    }


def markdown() -> str:
    lines = ["| Panel | Query | What it means |", "|---|---|---|"]
    for _, row_panels in ROWS:
        for p in row_panels:
            exprs = "<br>".join(f"`{t.expr.replace('|', chr(92) + '|')}`" for t in p.targets)
            lines.append(f"| {p.title} | {exprs} | {p.description} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", action="store_true", help="print the panel table")
    args = parser.parse_args()
    if args.markdown:
        print(markdown())
        return 0
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
