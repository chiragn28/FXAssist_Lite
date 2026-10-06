"""Answer cache and rate limiter: CAC-01 to CAC-06 (ADR-010)."""

from __future__ import annotations

import asyncio
import time

import fakeredis
import pytest

from bench.guard import BenchmarkRefused, require_cache_off
from fxassist_agent.prompts import PROMPT_VERSION
from fxassist_gateway.answers import CACHE_NAMESPACE, cache_key, normalise_question, ttl_for
from fxassist_gateway.redis_state import BUCKET_LUA, RateLimiter, RedisGuard
from gw_helpers import metric_total

QUESTION = "What leverage limits apply to retail clients?"


def test_second_identical_question_is_served_from_cache(gw) -> None:
    first = gw.ask(QUESTION).json()
    second = gw.ask("  what LEVERAGE limits apply to retail clients ").json()
    assert first["cached"] is False and second["cached"] is True
    assert second["answer"] == first["answer"]
    assert gw.mock_stats()["by_kind"]["answer"] == 1


# --- CAC-01: Redis down -----------------------------------------------------------------------


def test_cac01_redis_down_fails_open_and_reports_degraded(gateway_factory) -> None:
    gw = gateway_factory(
        rate_limit_per_minute=60, rate_limit_burst=8, rate_limit_fallback_divisor=4
    )
    gw.redis_server.connected = False
    first = gw.ask(QUESTION)
    second = gw.ask(QUESTION)
    assert first.status_code == second.status_code == 200
    assert second.json()["cached"] is False  # cache skipped, request still answered
    assert metric_total("fxa_cache_lookups", result="skip") >= 2
    with gw.client() as c:
        ready = c.get("/readyz")
    assert ready.status_code == 200 and ready.json()["status"] == "degraded"
    assert ready.json()["checks"]["redis"]["ok"] is False


def test_cac01_rate_limit_falls_back_to_a_stricter_local_limit(gateway_factory) -> None:
    gw = gateway_factory(
        rate_limit_per_minute=60, rate_limit_burst=8, rate_limit_fallback_divisor=4
    )
    gw.redis_server.connected = False
    codes = [gw.ask("help").status_code for _ in range(3)]
    assert codes == [200, 200, 429]  # burst 8 / 4 = 2 while Redis is down, never unlimited
    assert metric_total("fxa_rate_limit_decisions", backend="memory", allowed="false") >= 1


def test_cac01_redis_recovers_without_a_restart(gateway_factory) -> None:
    gw = gateway_factory(redis_retry_after_s=0.2)
    gw.redis_server.connected = False
    assert gw.ask(QUESTION).status_code == 200
    gw.redis_server.connected = True
    time.sleep(0.3)
    gw.ask(QUESTION)
    assert gw.ask(QUESTION).json()["cached"] is True


# --- CAC-02: versions in the key ------------------------------------------------------------------


def test_cac02_key_changes_with_corpus_prompt_and_model() -> None:
    base = cache_key(QUESTION, "v1", PROMPT_VERSION, "m@http://a")
    assert base == cache_key(QUESTION, "v1", PROMPT_VERSION, "m@http://a")
    assert base != cache_key(QUESTION, "v2", PROMPT_VERSION, "m@http://a")
    assert base != cache_key(QUESTION, "v1", "other-prompts", "m@http://a")
    assert base != cache_key(QUESTION, "v1", PROMPT_VERSION, "m@http://b")


def test_cac02_reingest_means_no_stale_answer(gw) -> None:
    assert gw.ask(QUESTION).json()["corpus_version"] == "v1"
    assert gw.ask(QUESTION).json()["cached"] is True
    gw.store.set_corpus_info({"corpus_version": "v2"})  # what `make ingest` does on change
    fresh = gw.ask(QUESTION).json()
    assert fresh["cached"] is False and fresh["corpus_version"] == "v2"


def test_cac02_prompt_version_tracks_the_prompt_text() -> None:
    import hashlib

    from fxassist_agent import prompts

    material = "\x00".join(
        [
            prompts.SYSTEM_PROMPT,
            prompts.ADVICE_ADDENDUM,
            prompts.GRADER_SYSTEM,
            prompts.REWRITE_SYSTEM,
        ]
    )
    assert hashlib.sha256(material.encode()).hexdigest()[:12] == PROMPT_VERSION


# --- CAC-03: what gets cached, and for how long ---------------------------------------------------


def test_cac03_ttl_policy() -> None:
    ttl = lambda outcome: ttl_for(outcome, normal_s=3600, short_s=120)  # noqa: E731
    assert ttl("answered") == ttl("declined_advice") == 3600
    assert ttl("abstained") == ttl("out_of_scope") == 120
    assert ttl("error") == ttl("clarify") == ttl("refused") == 0


def test_cac03_errors_are_never_cached(gw) -> None:
    gw.mock_config(error_status=503)
    assert gw.ask(QUESTION).status_code == 503
    gw.mock_config(error_status=None)
    body = gw.ask(QUESTION).json()
    assert body["outcome"] == "answered" and body["cached"] is False


def test_cac03_abstentions_get_the_short_ttl(gateway_factory) -> None:
    gw = gateway_factory(cache_short_ttl_s=7)
    gw.mock_config(reply="INSUFFICIENT_CONTEXT")
    assert gw.ask(QUESTION).json()["outcome"] in ("abstained", "out_of_scope")
    client = fakeredis.FakeStrictRedis(server=gw.redis_server)
    keys = client.keys(CACHE_NAMESPACE + "*")
    assert len(keys) == 1 and 0 < client.ttl(keys[0]) <= 7


# --- CAC-04: cache poisoning ------------------------------------------------------------------


@pytest.mark.parametrize(
    "crafted",
    [
        'What is a pip?", "v1", "x", "m"]',  # tries to close the JSON array early
        "What is a pip?\x00v1",
        "fxa:answer:v1:" + "0" * 64,
        "What is a pip?\n\nv2",
    ],
)
def test_cac04_crafted_questions_cannot_shape_the_key(crafted) -> None:
    key = cache_key(crafted, "v1", "p", "m")
    assert key.startswith(CACHE_NAMESPACE) and len(key) == len(CACHE_NAMESPACE) + 64
    assert key != cache_key("What is a pip?", "v1", "p", "m")
    assert all(c in "0123456789abcdef" for c in key[len(CACHE_NAMESPACE) :])


def test_cac04_normalisation_is_meaning_preserving_only() -> None:
    assert normalise_question("  What  is a PIP?? ") == normalise_question("what is a pip")
    assert normalise_question("ﬁnance") == normalise_question("finance")  # NFKC ligature
    assert normalise_question("What is a pip") != normalise_question("What is a lot")


# --- CAC-05: benchmarks refuse to run with the cache on ---------------------------------------------


def test_cac05_benchmark_guard_refuses_a_cached_gateway(gateway_factory) -> None:
    cached = gateway_factory()
    with pytest.raises(BenchmarkRefused, match="FXA_CACHE_ENABLED=false"):
        require_cache_off(cached.url, cached.key)
    uncached = gateway_factory(cache_enabled=False)
    assert require_cache_off(uncached.url, uncached.key)["cache_enabled"] is False


# --- CAC-06: clock skew and Redis restarts -------------------------------------------------------


def test_cac06_limiter_uses_redis_time_not_the_gateway_clock() -> None:
    assert "redis.call('TIME')" in BUCKET_LUA


@pytest.mark.anyio
async def test_cac06_redis_restart_refills_buckets_rather_than_locking_out() -> None:
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    limiter = RateLimiter(RedisGuard(client, 1.0), per_minute=60, burst=2, fallback_divisor=4)
    assert [(await limiter.check("k")).allowed for _ in range(3)] == [True, True, False]
    await client.flushall()  # a restart without persistence loses every bucket
    assert (await limiter.check("k")).allowed  # errs towards allowing, never locks out
    await asyncio.sleep(0)
