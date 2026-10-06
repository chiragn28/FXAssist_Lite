"""LLM-09: the mock can misbehave in every way the adapter must survive.

Runs the real server on a free port (CI-01), because disconnects and streaming quirks only
show up over a real socket.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from fxassist_mock_llm.app import Behaviour, create_app
from fxassist_mock_llm.server import serve

EXCERPTS = (
    '<excerpts>\n<excerpt label="S1" source="ESMA: Measures">\n'
    "Leverage is limited to 30:1 for major pairs. Other rules apply.\n</excerpt>\n\n"
    '<excerpt label="S2" source="FCA: Policy">\nA margin close-out rule applies.\n</excerpt>\n'
    "</excerpts>\n\n<question>\nWhat leverage applies?\n</question>"
)


def chat(client: httpx.Client, *, stream: bool = True, json_mode: bool = False, **extra):
    body = {
        "model": "mock-llm",
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": EXCERPTS}],
        "stream": stream,
        **extra,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return client.post("/v1/chat/completions", json=body)


def content_of(sse_text: str) -> str:
    out = []
    for line in sse_text.replace("\r\n", "\n").split("\n"):
        if line.startswith("data:"):
            data = line[5:].strip()
            if data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            for choice in obj.get("choices", []):
                out.append((choice.get("delta") or {}).get("content") or "")
    return "".join(out)


@pytest.fixture
def mock():
    with serve(create_app(Behaviour())) as server, httpx.Client(base_url=server.url) as client:
        yield client


def configure(client: httpx.Client, **patch) -> None:
    client.post("/_mock/config", json=patch).raise_for_status()


def test_llm09_answers_like_a_rag_model(mock) -> None:
    answer = content_of(chat(mock).text)
    assert answer == "According to the documents: Leverage is limited to 30:1 for major pairs. [S1]"
    grade = json.loads(
        chat(mock, stream=False, json_mode=True).json()["choices"][0]["message"]["content"]
    )
    assert grade == {"relevant": ["S1", "S2"]}


def test_llm09_unknown_model_is_404_like_ollama(mock) -> None:
    response = mock.post(
        "/v1/chat/completions",
        json={"model": "nope", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 404 and "not found" in response.json()["error"]["message"]


def test_llm09_error_count_fails_then_recovers(mock) -> None:
    configure(mock, error_status=503, error_count=2, retry_after=3)
    first, second, third = chat(mock), chat(mock), chat(mock)
    assert (first.status_code, second.status_code, third.status_code) == (503, 503, 200)
    assert first.headers["retry-after"] == "3"


def test_llm09_latency_and_slow_first_token(mock) -> None:
    configure(mock, latency_ms=150, first_token_ms=150)
    started = time.monotonic()
    chat(mock)
    assert time.monotonic() - started >= 0.3


def test_llm09_empty_answer(mock) -> None:
    configure(mock, empty=True)
    response = chat(mock)
    assert response.status_code == 200 and content_of(response.text) == ""


def test_llm09_malformed_chunks_are_emitted(mock) -> None:
    configure(mock, malformed_every=2)
    assert '"unterminated' in chat(mock).text


def test_llm09_mid_stream_disconnect(mock) -> None:
    configure(mock, disconnect_after=2)
    with pytest.raises(httpx.RemoteProtocolError):
        chat(mock)
    assert mock.get("/_mock/stats").json()["streams_dropped"] == 1


@pytest.mark.parametrize("flavour", ["openai", "ollama", "vllm", "quirky"])
def test_llm09_streaming_flavours_carry_the_same_answer(mock, flavour) -> None:
    configure(mock, flavour=flavour)
    text = chat(mock).text
    assert content_of(text).endswith("[S1]")
    assert text.rstrip().endswith("[DONE]")
    if flavour == "quirky":
        assert "\r\n" in text and "data:{" in text and ": keep-alive" in text


def test_llm09_hang_needs_a_client_timeout(mock) -> None:
    configure(mock, hang=True)
    with pytest.raises(httpx.ReadTimeout):
        mock.post(
            "/v1/chat/completions",
            json={"model": "mock-llm", "messages": [{"role": "user", "content": "x"}]},
            timeout=httpx.Timeout(5, read=0.3),
        )


def test_llm09_reset_restores_startup_behaviour(mock) -> None:
    configure(mock, error_status=500)
    assert chat(mock).status_code == 500
    mock.delete("/_mock/config").raise_for_status()
    assert chat(mock).status_code == 200
