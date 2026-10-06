"""Observability: OBS-01 to OBS-05 and DEP-03 (ADR-012)."""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from fxassist_gateway import metrics as fxa_metrics
from fxassist_gateway.config import GatewaySettings
from fxassist_gateway.telemetry import RedactingFilter, trace_target
from fxassist_mock_llm.server import serve
from gw_helpers import EXTRA_SPAN_PROCESSORS, SPANS, histogram_points, metric_points

ROOT = Path(__file__).resolve().parents[3]
QUESTION = "What leverage limits apply to retail clients?"


def delta(name: str, before: tuple[float, int], **attributes: str) -> tuple[float, int]:
    points = histogram_points(name, **attributes)
    total = sum(p.sum for p in points), sum(p.count for p in points)
    return total[0] - before[0], total[1] - before[1]


def snapshot(name: str, **attributes: str) -> tuple[float, int]:
    points = histogram_points(name, **attributes)
    return sum(p.sum for p in points), sum(p.count for p in points)


# --- OBS-01: metric labels stay low-cardinality ---------------------------------------------


def test_obs01_every_metric_attribute_is_on_the_allowlist(gw) -> None:
    # Traffic that touches every code path: answers, cache hits, errors, guards, 401, 422.
    for question in (QUESTION, QUESTION, "help", "Should I buy EUR/USD now?", "bread recipes?"):
        gw.ask(question)
    gw.ask(QUESTION, key="fxa_00000000_" + "A" * 43)
    gw.ask("x" * 2000)
    gw.mock_config(error_status=503)
    gw.ask("What is a pip?")
    seen: dict[str, set[str]] = {}
    for name, attributes, _ in metric_points():
        if not name.startswith("fxa_"):
            continue  # FastAPI's own http.server.* metrics label by route template only
        for key, value in attributes.items():
            seen.setdefault(key, set()).add(str(value))
    unknown = set(seen) - set(fxa_metrics.ALLOWED_ATTRIBUTES)
    assert not unknown, f"attribute keys not on the allowlist: {unknown}"
    for key, allowed in fxa_metrics.ALLOWED_ATTRIBUTES.items():
        values = seen.get(key, set())
        if allowed is not None:
            assert values <= allowed, f"{key}: unexpected values {values - allowed}"
        assert len(values) <= 25, f"{key} has {len(values)} distinct values"
    everything = " ".join(v for values in seen.values() for v in values)
    assert gw.key not in everything and "leverage" not in everything.lower()
    assert not re.search(r"\b[0-9a-f]{32}\b", everything), "a request ID leaked into labels"


def test_obs01_every_call_site_uses_literal_allowlisted_label_keys() -> None:
    """Static check over the source: `metrics.<instrument>.add/record(value, {...})` calls pass
    a dict literal whose keys are string constants on the allowlist (no computed keys)."""
    import ast

    keys: set[str] = set()
    for path in (ROOT / "services/gateway/src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("add", "record")
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "metrics"
            ):
                continue
            for arg in node.args[1:]:
                assert isinstance(arg, ast.Dict), (
                    f"{path.name}:{node.lineno} attributes not a literal"
                )
                for key in arg.keys:
                    assert isinstance(key, ast.Constant), f"{path.name}:{node.lineno} computed key"
                    keys.add(key.value)
    assert keys, "found no metric call sites; update the test"
    assert keys <= set(fxa_metrics.ALLOWED_ATTRIBUTES), keys - set(fxa_metrics.ALLOWED_ATTRIBUTES)


# --- OBS-02: no secrets or personal data in logs and traces --------------------------------------


def test_obs02_logs_are_redacted(gw) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("test").warning("client sent %s", f"Authorization: Bearer {gw.key}")
        logging.getLogger("test").warning("dsn host=db password=hunter2 user=x")
        logging.getLogger("test").warning("langfuse sk-lf-1234567890abcdef")
        gw.ask(QUESTION)
    finally:
        root.removeHandler(handler)
    text = stream.getvalue()
    assert gw.key not in text and gw.key[13:] not in text
    assert "hunter2" not in text and "sk-lf-1234567890abcdef" not in text
    assert "<redacted>" in text


def test_obs02_request_log_and_spans_carry_no_question_or_key_by_default(gw) -> None:
    SPANS.clear()
    gw.ask(QUESTION)
    spans = SPANS.get_finished_spans()
    names = {s.name for s in spans}
    assert {"fxassist.ask", "agent.ask", "agent.retrieve", "llm.chat"} <= names
    dumped = json.dumps([dict(s.attributes) for s in spans], default=str)
    assert "leverage" not in dumped.lower() and gw.key not in dumped
    assert "gen_ai.prompt" not in dumped and "input.value" not in dumped
    rows = json.dumps(gw.logs.rows, default=str)
    assert "leverage" not in rows.lower() and gw.key not in rows


def test_obs02_content_tracing_is_opt_in_truncated_and_redacted(gateway_factory) -> None:
    gw = gateway_factory(trace_content=True)
    SPANS.clear()
    question = "What is a margin call? My key is fxa_0123abcd_" + "Z" * 43
    gw.ask(question)
    spans = {s.name: s for s in SPANS.get_finished_spans()}
    request_input = spans["fxassist.ask"].attributes["input.value"]
    assert "margin call" in request_input and "fxa_<redacted>" in request_input
    assert "Z" * 43 not in json.dumps([dict(s.attributes) for s in spans.values()])
    llm = [s for s in SPANS.get_finished_spans() if s.name == "llm.chat"]
    assert all(len(s.attributes["gen_ai.prompt"]) <= 1000 + len("...[truncated]") for s in llm)


def test_obs02_agent_spans_nest_under_the_request_span(gw) -> None:
    SPANS.clear()
    gw.ask(QUESTION)
    spans = SPANS.get_finished_spans()
    root = next(s for s in spans if s.name == "fxassist.ask")
    # One trace: FastAPI's server span, ours, the agent nodes, the LLM calls and, through the
    # traceparent header, even the (mock) model server's own spans.
    assert {s.context.trace_id for s in spans} == {root.context.trace_id}
    ours = {s.context.span_id for s in spans if s.name.startswith(("agent.", "fxassist."))}
    graph = next(s for s in spans if s.name == "agent.ask")
    assert graph.parent.span_id == root.context.span_id and graph.context.span_id in ours
    llm = next(s for s in spans if s.name == "llm.chat")
    assert llm.attributes["gen_ai.request.model"] == "mock-llm"


# --- OBS-03: dashboard panels are documented and say what "no data" means ----------------------


DASHBOARD = json.loads((ROOT / "observability/grafana/dashboards/fxassist.json").read_text())
PANELS = [p for p in DASHBOARD["panels"] if p["type"] != "row"]


def test_obs03_dashboard_json_is_generated_from_the_spec() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "observability/grafana/build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["build_dashboard"] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    assert module.build() == DASHBOARD, "run `make dashboard` after editing the spec"


@pytest.mark.parametrize("panel", PANELS, ids=[p["title"] for p in PANELS])
def test_obs03_every_panel_has_a_description_query_and_no_data_text(panel) -> None:
    assert len(panel["description"]) > 40
    assert panel["fieldConfig"]["defaults"]["noValue"].startswith("No data")
    assert panel["targets"] and all(t["expr"].strip() for t in panel["targets"])


def test_obs03_queries_and_alerts_only_use_metrics_that_exist() -> None:
    exported = {"up"}
    for name in re.findall(r'"(fxa_[a-z_]+)"', (ROOT / fxa_metrics.__file__).read_text()):
        exported |= {f"{name}_total", f"{name}_seconds_bucket", f"{name}_bucket", name}
    telemetry = (ROOT / "services/gateway/src/fxassist_gateway/telemetry.py").read_text()
    exported |= set(re.findall(r'"(fxa_[a-z_]+)"', telemetry))
    exprs = [t["expr"] for p in PANELS for t in p["targets"]]
    rules = yaml.safe_load((ROOT / "observability/prometheus/rules/fxassist.yml").read_text())
    exprs += [r["expr"] for g in rules["groups"] for r in g["rules"]]
    used = {m for e in exprs for m in re.findall(r"\b(fxa_[a-z_]+)", e)}
    assert used <= exported, used - exported
    for group in rules["groups"]:
        for rule in group["rules"]:
            assert (ROOT / rule["annotations"]["runbook"]).exists(), rule["alert"]


# --- OBS-04: metrics are not on the public API port ---------------------------------------


def test_obs04_api_port_does_not_serve_metrics(gw) -> None:
    with gw.client() as c:
        assert c.get("/metrics").status_code == 404
    assert GatewaySettings(_env_file=None).metrics_host == "127.0.0.1"


def test_obs04_compose_never_publishes_the_metrics_port() -> None:
    compose = yaml.safe_load((ROOT / "deploy/compose/compose.yaml").read_text())
    gateway = compose["services"]["gateway"]
    assert all("9464" not in str(p) for p in gateway.get("ports", []))
    assert gateway["environment"]["FXA_METRICS_PORT"] == "9464"
    prom = (ROOT / "observability/prometheus/prometheus.yml").read_text()
    assert "gateway:9464" in prom


# --- OBS-05: queue, retrieval, time to first token and total are measured separately -------


def test_obs05_ttft_excludes_retrieval_and_queueing(gateway_factory) -> None:
    gw = gateway_factory(cache_enabled=False)
    gw.mock_config(first_token_ms=400, token_ms=5)
    ttft0 = snapshot("fxa_stage_duration", stage="ttft")
    retrieval0 = snapshot("fxa_stage_duration", stage="retrieval")
    total0 = snapshot("fxa_request_duration", outcome="answered", cached="false")
    assert gw.ask(QUESTION).json()["outcome"] == "answered"
    ttft_sum, ttft_n = delta("fxa_stage_duration", ttft0, stage="ttft")
    retrieval_sum, retrieval_n = delta("fxa_stage_duration", retrieval0, stage="retrieval")
    total_sum, total_n = delta("fxa_request_duration", total0, outcome="answered", cached="false")
    assert ttft_n == retrieval_n == total_n == 1
    assert 0.4 <= ttft_sum < 0.8  # the mock's first-token delay, nothing else
    assert retrieval_sum < 0.2
    # grader (0.4 s first token) + answer (0.4 s) + retrieval: the total is clearly larger
    assert total_sum > ttft_sum + 0.3


def test_obs05_queue_time_is_its_own_stage(gateway_factory) -> None:
    gw = gateway_factory(cache_enabled=False, max_concurrent_runs=1)
    gw.mock_config(token_ms=30)
    queue0 = snapshot("fxa_stage_duration", stage="queue")
    threads = [
        threading.Thread(target=gw.ask, args=(q,))
        for q in ("What is a pip?", "What is a margin call?")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    queue_sum, queue_n = delta("fxa_stage_duration", queue0, stage="queue")
    assert queue_n == 2 and queue_sum > 0.2  # the second waited for the first's slot


# --- DEP-03: a broken tracing backend never affects requests ----------------------------------


def _fake_otlp_backend(mode: str) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/traces")
    async def traces(request: Request):
        if mode == "429":
            return JSONResponse({"error": "rate limited"}, status_code=429)
        while not await request.is_disconnected():  # "hang": never answer
            await __import__("asyncio").sleep(0.05)
        return JSONResponse({}, status_code=499)

    return app


@pytest.mark.parametrize("mode", ["unreachable", "429", "hang"])
def test_dep03_tracing_backend_failures_do_not_touch_requests(gateway_factory, mode) -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    gw = gateway_factory(cache_enabled=False)
    baseline = []
    for _ in range(3):
        started = time.monotonic()
        assert gw.ask(QUESTION).status_code == 200
        baseline.append(time.monotonic() - started)

    def run(endpoint: str) -> None:
        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=0.5)
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=16,
            max_export_batch_size=16,
            schedule_delay_millis=50,
            export_timeout_millis=500,
        )
        EXTRA_SPAN_PROCESSORS.processors.append(processor)
        try:
            timings = []
            for _ in range(5):
                started = time.monotonic()
                assert gw.ask(QUESTION).status_code == 200
                timings.append(time.monotonic() - started)
            assert max(timings) < max(baseline) + 0.5, (timings, baseline)
        finally:
            EXTRA_SPAN_PROCESSORS.processors.remove(processor)
            started = time.monotonic()
            processor.shutdown()  # bounded by the export timeout, not by the backend
            assert time.monotonic() - started < 5

    if mode == "unreachable":
        run("http://127.0.0.1:9/v1/traces")
    else:
        with serve(_fake_otlp_backend(mode)) as backend:
            run(f"{backend.url}/v1/traces")


def test_dep03_langfuse_target_is_built_from_settings_without_logging_secrets() -> None:
    settings = GatewaySettings(
        _env_file=None,
        langfuse_public_key="pk-lf-abc",
        langfuse_secret_key="sk-lf-secret",  # noqa: S106 - fake test value
    )
    target = trace_target(settings)
    assert target.endpoint == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    assert target.headers["Authorization"].startswith("Basic ")
    assert target.headers["x-langfuse-ingestion-version"] == "4"
    assert "secret" not in target.name
    assert trace_target(GatewaySettings(_env_file=None)) is None  # off unless configured
