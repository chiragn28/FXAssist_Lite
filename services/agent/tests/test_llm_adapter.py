"""The LLM adapter against a real (mock) server: LLM-01 to LLM-07, LLM-09.

The mock runs on a free localhost port (CI-01). One contract test also runs against Ollama
when it is up (LLM-07: "tests run against mock and Ollama"); it is skipped otherwise.
"""

from __future__ import annotations

import os
import threading
import time

import httpx
import pytest

from fxassist_agent.control import RunControl
from fxassist_agent.llm import (
    CircuitBreaker,
    CircuitOpenError,
    LLMCancelledError,
    LLMContextError,
    LLMEmptyResponseError,
    LLMStreamError,
    LLMTimeoutError,
    LLMUnavailableError,
    OpenAICompatLLM,
    StreamResult,
    apply_chunk,
    sse_data,
)
from fxassist_mock_llm.app import Behaviour, create_app
from fxassist_mock_llm.server import serve

MESSAGES = [{"role": "user", "content": "Say OK."}]


@pytest.fixture(scope="module")
def server():
    with serve(create_app(Behaviour(models=["mock-llm"]))) as s:
        yield s


@pytest.fixture
def mock(server):
    client = httpx.Client(base_url=server.url, timeout=5)
    client.delete("/_mock/config").raise_for_status()  # fresh behaviour and stats
    yield client
    client.close()


def configure(mock: httpx.Client, **patch) -> None:
    mock.post("/_mock/config", json=patch).raise_for_status()


def stats(mock: httpx.Client) -> dict:
    return mock.get("/_mock/stats").json()


def adapter(server, **overrides) -> OpenAICompatLLM:
    options = {
        "api_key": "x",
        "timeout_s": 5.0,
        "read_timeout_s": 2.0,
        "backoff_base_s": 0.01,
        "backoff_max_s": 0.05,
        "breaker": CircuitBreaker(failure_threshold=50),
    } | overrides
    return OpenAICompatLLM(f"{server.url}/v1", "mock-llm", **options)


# --- Happy path and format normalisation (LLM-07) ------------------------------------------


@pytest.mark.parametrize("flavour", ["openai", "ollama", "vllm", "quirky"])
def test_llm07_every_streaming_flavour_gives_the_same_text(server, mock, flavour) -> None:
    configure(mock, flavour=flavour, reply="Hello there, world.")
    control = RunControl()
    text = adapter(server).complete(MESSAGES, max_tokens=20, control=control)
    assert text == "Hello there, world."
    call = control.llm_calls[0]
    assert call.ttft_s is not None and call.total_s >= call.ttft_s
    if flavour in ("ollama", "vllm"):
        assert call.completion_tokens == 3  # usage chunk with choices: []


def test_llm07_non_streaming_json_body_is_accepted() -> None:
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    llm = OpenAICompatLLM("http://x/v1", "m", api_key="x", transport=httpx.MockTransport(handler))
    assert llm.complete(MESSAGES, max_tokens=5) == "hi"


def test_llm07_sse_parsing_tolerates_quirks() -> None:
    lines = ["event: message", ": comment", 'data:{"a":1}\r', "data: [DONE]", ""]
    assert list(sse_data(iter(lines))) == ['{"a":1}', "[DONE]"]
    result = StreamResult()
    apply_chunk({"choices": [{"delta": {"content": None}}]}, result, 0.0)
    apply_chunk({"choices": [{"text": "legacy"}]}, result, 1.0)
    apply_chunk({"choices": [], "usage": {"completion_tokens": 7}}, result, 2.0)
    assert result.parts == ["legacy"] and result.first_token_at == 1.0
    assert result.completion_tokens == 7


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="no Ollama in CI (CI-05)")
def test_llm07_contract_with_real_ollama() -> None:
    """The same adapter against the real Ollama server, when it is running locally."""
    base = os.environ.get("FXA_OLLAMA_URL", "http://localhost:11434/v1")
    model = os.environ.get("FXA_LLM_MODEL", "qwen2.5:3b-instruct")
    llm = OpenAICompatLLM(base, model, api_key="x", timeout_s=60)
    try:
        llm.check_model()
    except Exception as exc:  # not running or model not pulled: nothing to compare against
        pytest.skip(f"Ollama not available: {exc}")
    control = RunControl()
    text = llm.complete(
        [{"role": "user", "content": "Reply with the single word OK."}],
        max_tokens=5,
        control=control,
    )
    assert text.strip()
    assert control.llm_calls[0].completion_tokens  # Ollama sends usage when asked
    assert control.llm_calls[0].ttft_s is not None


# --- LLM-01: timeouts ---------------------------------------------------------------------


def test_llm01_server_that_never_answers_times_out_and_lets_go(server, mock) -> None:
    configure(mock, hang=True)
    started = time.monotonic()
    with pytest.raises(LLMTimeoutError, match="first token"):
        adapter(server, read_timeout_s=0.3).complete(MESSAGES, max_tokens=5)
    assert time.monotonic() - started < 2
    deadline = time.monotonic() + 3  # no hanging connection: the mock sees the client leave
    while stats(mock)["streams_cancelled"] < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stats(mock)["streams_cancelled"] == 1


def test_llm01_stream_slower_than_the_total_budget_times_out(server, mock) -> None:
    configure(mock, token_ms=150, reply="one two three four five six seven eight")
    with pytest.raises(LLMTimeoutError, match="did not finish"):
        adapter(server, timeout_s=0.5).complete(MESSAGES, max_tokens=50)


# --- LLM-02: stream breaks halfway ------------------------------------------------------------


def test_llm02_stream_broken_after_content_is_an_error_not_a_short_answer(server, mock) -> None:
    configure(mock, disconnect_after=2, reply="one two three four")
    with pytest.raises(LLMStreamError, match="broke after 2"):
        adapter(server).complete(MESSAGES, max_tokens=20)
    assert stats(mock)["requests"] == 1  # not retried: it may have been half-processed


def test_llm02_stream_ending_without_finish_is_an_error() -> None:
    def handler(request):
        body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatLLM("http://x/v1", "m", api_key="x", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMStreamError, match="without finishing"):
        llm.complete(MESSAGES, max_tokens=5)


# --- LLM-03: empty answers -------------------------------------------------------------------


def test_llm03_empty_answer_is_retried_once_then_fails(server, mock) -> None:
    configure(mock, empty=True)
    with pytest.raises(LLMEmptyResponseError):
        adapter(server).complete(MESSAGES, max_tokens=5)
    assert stats(mock)["requests"] == 2


def test_llm03_empty_then_good_answer_succeeds() -> None:
    replies = iter(["", "fine"])

    def handler(request):
        text = next(replies)
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    llm = OpenAICompatLLM("http://x/v1", "m", api_key="x", transport=httpx.MockTransport(handler))
    assert llm.complete(MESSAGES, max_tokens=5) == "fine"


# --- LLM-04: 429/503, backoff, circuit breaker --------------------------------------------------


def test_llm04_transient_503s_are_retried_with_backoff(server, mock) -> None:
    configure(mock, error_status=503, error_count=2)
    control = RunControl()
    assert adapter(server).complete(MESSAGES, max_tokens=20, control=control)
    assert stats(mock)["requests"] == 3 and control.llm_calls[0].attempts == 3


def test_llm04_retries_are_bounded(server, mock) -> None:
    configure(mock, error_status=429)
    with pytest.raises(LLMUnavailableError, match="gave up after 3"):
        adapter(server, max_retries=2).complete(MESSAGES, max_tokens=5)
    assert stats(mock)["requests"] == 3


def test_llm04_backoff_has_full_jitter_and_honours_retry_after() -> None:
    llm = OpenAICompatLLM("http://x/v1", "m", api_key="x", backoff_base_s=1, backoff_max_s=8)
    llm._rng = lambda: 0.5
    assert [llm._backoff(n, None) for n in (1, 2, 3, 4, 5)] == [0.5, 1.0, 2.0, 4.0, 4.0]
    llm._rng = lambda: 0.0
    assert llm._backoff(1, None) == 0.0  # full jitter: anywhere from 0 to the ceiling
    assert llm._backoff(1, 3.0) == 3.0 and llm._backoff(1, 60.0) == 8  # capped


def test_llm04_circuit_opens_and_fails_fast(server, mock) -> None:
    configure(mock, error_status=503)
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout_s=60)
    llm = adapter(server, max_retries=5, breaker=breaker)
    with pytest.raises(LLMUnavailableError):
        llm.complete(MESSAGES, max_tokens=5)
    assert breaker.state == "open" and stats(mock)["requests"] == 3
    control = RunControl()
    with pytest.raises(CircuitOpenError) as info:
        llm.complete(MESSAGES, max_tokens=5, control=control)
    assert stats(mock)["requests"] == 3  # no network call while open
    # Counted as a failed call, not "ok" (found by the hanging-model drill, 2026-10-07).
    assert control.llm_calls[0].error == "llm_circuit_open"
    assert 0 < info.value.retry_after <= 60


def test_llm04_half_open_lets_one_trial_through_and_closes_on_success() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_s=10, clock=lambda: now[0])
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    now[0] = 10.0
    assert breaker.state == "half_open"
    breaker.before_call()  # the trial
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # only one trial at a time
    breaker.record_success()
    assert breaker.state == "closed"
    breaker.before_call()


def test_llm04_failed_trial_reopens_the_circuit() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=10, clock=lambda: now[0])
    breaker.record_failure()
    now[0] = 11.0
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == "open" and breaker.opened_count == 2


# --- LLM-05 (adapter part): cancellation closes the upstream stream ------------------------


def test_llm05_cancel_stops_the_stream_and_closes_the_connection(server, mock) -> None:
    configure(mock, token_ms=100, reply=" ".join(["word"] * 100))
    control = RunControl()
    errors: list[Exception] = []

    def run() -> None:
        try:
            adapter(server, timeout_s=30).complete(MESSAGES, max_tokens=200, control=control)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 3
    while stats(mock)["active_streams"] == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    control.cancel()
    thread.join(3)
    assert not thread.is_alive()
    assert isinstance(errors[0], LLMCancelledError)
    deadline = time.monotonic() + 3
    while stats(mock)["streams_cancelled"] < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    s = stats(mock)
    assert s["streams_cancelled"] == 1 and s["active_streams"] == 0 and s["streams_completed"] == 0


# --- LLM-06: prompt too long for the context window -----------------------------------------


def test_llm06_oversized_prompt_is_rejected_before_sending(server, mock) -> None:
    llm = adapter(server, max_context_tokens=1000)
    with pytest.raises(LLMContextError, match=r"> the model's context window of 1000 tokens"):
        llm.complete([{"role": "user", "content": "x" * 3000}], max_tokens=200)
    assert stats(mock)["requests"] == 0


def test_llm06_prompt_that_fits_is_sent(server, mock) -> None:
    llm = adapter(server, max_context_tokens=1000)
    assert llm.complete([{"role": "user", "content": "x" * 600}], max_tokens=200)


# --- LLM-09: malformed chunks are skipped and counted ------------------------------------------


def test_llm09_malformed_chunks_are_skipped_and_counted(server, mock) -> None:
    configure(mock, malformed_every=2, reply="a b c d")
    control = RunControl()
    assert adapter(server).complete(MESSAGES, max_tokens=20, control=control) == "a b c d"
    assert control.llm_calls[0].malformed_chunks == 2


def test_llm09_in_band_error_object_is_an_error() -> None:
    def handler(request):
        body = b'data: {"error": {"message": "CUDA out of memory"}}\n\n'
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatLLM("http://x/v1", "m", api_key="x", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMStreamError, match="out of memory"):
        llm.complete(MESSAGES, max_tokens=5)
