"""Dependency failures: DEP-01 (Qdrant), DEP-02 (PostgreSQL), DEP-04 (start order), readiness."""

from __future__ import annotations

import time

QUESTION = "What leverage limits apply to retail clients?"


def wait_for(predicate, timeout: float = 5.0, what: str = "condition") -> None:
    """Readiness polling instead of fixed sleeps (CI-01)."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


def readyz(gw) -> tuple[int, dict]:
    with gw.client() as c:
        response = c.get("/readyz")
    return response.status_code, response.json()


def test_ready_when_everything_is_up(gw) -> None:
    code, body = readyz(gw)
    assert code == 200 and body["status"] == "ready"
    assert set(body["checks"]) == {"qdrant", "llm", "auth", "redis", "postgres"}


# --- DEP-01: Qdrant down ---------------------------------------------------------------------


def test_dep01_qdrant_down_gives_503_and_never_calls_the_model(gw) -> None:
    gw.qdrant.down = True
    response = gw.ask(QUESTION)
    assert response.status_code == 503 and response.headers["retry-after"]
    assert response.json()["error"]["code"] == "store_unavailable"
    assert gw.mock_stats()["requests"] == 0  # no documents, no answer: nothing made up
    code, body = readyz(gw)
    assert code == 503 and body["status"] == "not_ready"
    assert body["checks"]["qdrant"]["ok"] is False


def test_dep01_qdrant_failing_mid_request_is_also_503(gateway_factory) -> None:
    gw = gateway_factory(index_info_ttl_s=60)  # corpus version still cached from before
    assert gw.ask("What is a pip?").status_code == 200
    gw.qdrant.down = True
    response = gw.ask(QUESTION)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "store_unavailable"


def test_dep01_missing_collection_says_to_ingest(gw) -> None:
    gw.store.drop()
    response = gw.ask(QUESTION)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "index_not_ready"
    assert "make ingest" in response.json()["error"]["message"]


def test_dep01_recovery_needs_no_restart(gw) -> None:
    gw.qdrant.down = True
    assert gw.ask(QUESTION).status_code == 503
    gw.qdrant.down = False
    assert gw.ask(QUESTION).status_code == 200
    assert readyz(gw)[0] == 200


# --- DEP-02: PostgreSQL down ------------------------------------------------------------------


def test_dep02_postgres_down_requests_still_succeed_and_logs_are_buffered(gw) -> None:
    gw.keys.fail = True
    gw.logs.fail = True
    time.sleep(0.3)  # at least one failed key refresh (key_refresh_s=0.2)
    responses = [gw.ask(QUESTION) for _ in range(3)]
    assert [r.status_code for r in responses] == [200, 200, 200]  # cached key snapshot
    code, body = readyz(gw)
    assert code == 200 and body["status"] == "degraded"
    assert gw.logs.rows == [] and gw.components.logs.failing
    gw.keys.fail = False
    gw.logs.fail = False
    ids = {r.headers["x-request-id"] for r in responses}
    wait_for(lambda: ids <= {row["request_id"] for row in gw.logs.rows}, what="buffered logs")


def test_dep02_buffer_is_bounded_and_drops_are_counted(gateway_factory) -> None:
    from gw_helpers import metric_total

    gw = gateway_factory(log_queue_max=2, log_batch_size=1)
    gw.logs.fail = True
    before = metric_total("fxa_request_log_dropped")
    for _ in range(6):
        assert gw.ask("help").status_code == 200
    writer = gw.components.logs
    assert writer.queue.qsize() <= 2 and writer.dropped >= 3
    assert metric_total("fxa_request_log_dropped") - before == writer.dropped


# --- DEP-04: wrong start order ----------------------------------------------------------------


def test_dep04_starts_with_every_dependency_down_then_becomes_ready(gateway_factory) -> None:
    def everything_down(h) -> None:
        h.qdrant.down = True
        h.redis_server.connected = False
        h.keys.fail = True
        h.logs.fail = True
        h.mock_config(error_status=503)

    gw = gateway_factory(before_start=everything_down)  # must not crash on startup
    with gw.client() as c:
        assert c.get("/healthz").status_code == 200  # alive, so no restart loop
    code, body = readyz(gw)
    assert code == 503 and body["status"] == "not_ready"
    failing = {name for name, check in body["checks"].items() if not check["ok"]}
    assert failing == {"qdrant", "llm", "auth", "redis", "postgres"}
    assert gw.ask(QUESTION).status_code == 503  # auth not loaded: a clear "starting up"

    gw.qdrant.down = False
    gw.redis_server.connected = True
    gw.keys.fail = False
    gw.logs.fail = False
    gw.mock_config(error_status=None)
    wait_for(lambda: readyz(gw)[1]["status"] == "ready", what="ready without a restart")
    assert gw.ask(QUESTION).status_code == 200
