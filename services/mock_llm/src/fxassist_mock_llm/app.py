"""A mock OpenAI-compatible LLM server that can misbehave on purpose (ADR-003, LLM-09).

A mock that always answers instantly and perfectly makes every resilience test meaningless.
This one can add latency, delay the first token, fail with HTTP errors, return empty answers,
emit malformed chunks, drop the connection mid-stream, hang, and imitate the streaming quirks
of different servers (LLM-07).

Its answers are "RAG-aware" so the whole agent works against it in CI and on kind:
  - grader calls (JSON mode) mark every excerpt relevant: {"relevant": ["S1", "S2"]}
  - rewrite calls echo the question back as the search query
  - answer calls quote the first sentence of excerpt S1 and cite it, so citation and number
    checks pass; with no excerpts the answer is INSUFFICIENT_CONTEXT

Behaviour is set by FXA_MOCK_* environment variables at startup and can be changed at runtime:
  POST   /_mock/config   {"error_status": 503, "error_count": 2}   merge into the current config
  DELETE /_mock/config                                              back to the startup config
  GET    /_mock/stats                                               request counters
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

Flavour = Literal["openai", "ollama", "vllm", "quirky"]


class Behaviour(BaseModel):
    """What the mock does. Every field has a well-behaved default."""

    models: list[str] = Field(default_factory=lambda: ["mock-llm"])
    latency_ms: int = 0  # before the response starts (queueing, prompt processing)
    first_token_ms: int = 0  # extra delay before the first content chunk (slow first token)
    token_ms: int = 0  # delay between content chunks
    error_status: int | None = None  # e.g. 429 or 503
    error_count: int = 0  # fail the next N requests with error_status, then recover (0 = all)
    error_rate: float = 0.0  # or fail this fraction of requests at random (seeded)
    retry_after: int | None = None  # Retry-After header on errors
    empty: bool = False  # answer with no content
    malformed_every: int = 0  # emit an unparseable chunk before every Nth content chunk
    disconnect_after: int | None = None  # drop the connection after N content chunks
    hang: bool = False  # accept the request and never respond
    flavour: Flavour = "openai"  # streaming format quirks (LLM-07)
    reply: str | None = None  # fixed answer text instead of the RAG-aware one
    seed: int = 0

    @classmethod
    def from_env(cls) -> Behaviour:
        raw = os.environ.get("FXA_MOCK_CONFIG")
        base = cls.model_validate_json(raw) if raw else cls()
        if models := os.environ.get("FXA_MOCK_MODELS"):
            base.models = [m.strip() for m in models.split(",") if m.strip()]
        return base


class Stats(BaseModel):
    requests: int = 0
    streams_started: int = 0
    streams_completed: int = 0
    streams_cancelled: int = 0  # the client went away before the end (LLM-05 check)
    streams_dropped: int = 0  # the mock dropped the connection on purpose
    errors_returned: int = 0
    active_streams: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)


# --- Answers ------------------------------------------------------------------------------

_EXCERPT = re.compile(r'<excerpt label="(S\d+)"[^>]*>\n(.*?)\n</excerpt>', re.S)
_QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.S)
_SENTENCE = re.compile(r"(.+?[.!?])(\s|$)", re.S)


def call_kind(body: dict) -> str:
    system = next((m["content"] for m in body["messages"] if m.get("role") == "system"), "")
    if (body.get("response_format") or {}).get("type") == "json_object":
        return "grade"
    if "search query" in system:
        return "rewrite"
    if "<excerpts>" in (body["messages"][-1].get("content") or ""):
        return "answer"
    return "chat"


def make_reply(body: dict, behaviour: Behaviour) -> tuple[str, str]:
    """Returns (kind of call, reply text)."""
    kind = call_kind(body)
    if behaviour.empty:
        return kind, ""
    if behaviour.reply is not None:
        return kind, behaviour.reply
    user = body["messages"][-1].get("content") or ""
    excerpts = _EXCERPT.findall(user)
    if kind == "grade":
        return kind, json.dumps({"relevant": [label for label, _ in excerpts]})
    if kind == "rewrite":
        match = _QUESTION.search(user)
        return kind, (match.group(1) if match else user).strip()[:200]
    if kind == "answer":
        if not excerpts:
            return kind, "INSUFFICIENT_CONTEXT"
        label, text = excerpts[0]
        flat = " ".join(text.split())
        match = _SENTENCE.match(flat)
        sentence = (match.group(1) if match else flat)[:300].rstrip()
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        return kind, f"According to the documents: {sentence} [{label}]"
    return kind, "OK"


def split_tokens(text: str) -> list[str]:
    """Word-sized pieces that keep their leading space, like a real tokenizer's output."""
    return re.findall(r"\s*\S+", text) or ([text] if text else [])


# --- Streaming formats (LLM-07) ---------------------------------------------------------


class ChunkWriter:
    """Formats OpenAI-style chunks the way a given server would."""

    def __init__(self, flavour: Flavour, model: str):
        self.flavour = flavour
        self.model = model
        self.id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self.created = int(time.time())

    def _chunk(self, delta: dict, finish: str | None = None, **extra: Any) -> dict:
        obj = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if self.flavour == "ollama":
            obj["system_fingerprint"] = "fp_ollama"
        obj.update(extra)
        return obj

    def event(self, obj: dict | str) -> bytes:
        data = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
        if self.flavour == "quirky":
            return f"data:{data}\r\n\r\n".encode()  # no space after the colon, CRLF
        return f"data: {data}\n\n".encode()

    def preamble(self) -> list[bytes]:
        if self.flavour == "vllm":  # role first, with empty content
            return [self.event(self._chunk({"role": "assistant", "content": ""}))]
        if self.flavour == "quirky":
            return [
                b": keep-alive comment\r\n\r\n",
                b"event: message\r\n" + self.event(self._chunk({"role": "assistant"})),
                self.event(self._chunk({"content": None})),
            ]
        return []

    def content(self, piece: str, first: bool) -> bytes:
        delta = {"content": piece}
        if first and self.flavour in ("openai", "ollama"):
            delta = {"role": "assistant", "content": piece}  # what Ollama sends (captured)
        return self.event(self._chunk(delta))

    def ending(self, completion_tokens: int, prompt_tokens: int) -> list[bytes]:
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        finish = self._chunk({"content": ""} if self.flavour == "vllm" else {}, "stop")
        out = [self.event(finish)]
        if self.flavour in ("ollama", "vllm"):
            out.append(self.event({**self._chunk({}), "choices": [], "usage": usage}))
        out.append(self.event("[DONE]"))
        return out


# --- App ---------------------------------------------------------------------------------


class MockDroppedConnection(Exception):
    """Raised inside the stream to make the server abort the connection (mid-stream break)."""


def create_app(behaviour: Behaviour | None = None) -> FastAPI:
    app = FastAPI(title="FXAssist mock LLM", docs_url=None, redoc_url=None)
    startup = behaviour or Behaviour.from_env()
    state: dict[str, Any] = {
        "behaviour": startup.model_copy(deep=True),
        "stats": Stats(),
        "failures_left": startup.error_count,
        "rng": random.Random(startup.seed),  # noqa: S311 - test data, not security
    }

    def current() -> Behaviour:
        return state["behaviour"]

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def list_models() -> dict:
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "mock"} for m in current().models],
        }

    @app.post("/_mock/config")
    async def set_config(patch: dict) -> dict:
        merged = current().model_dump() | patch
        state["behaviour"] = Behaviour.model_validate(merged)
        state["failures_left"] = state["behaviour"].error_count
        state["rng"] = random.Random(state["behaviour"].seed)  # noqa: S311
        return state["behaviour"].model_dump()

    @app.delete("/_mock/config")
    async def reset_config() -> dict:
        state["behaviour"] = startup.model_copy(deep=True)
        state["failures_left"] = startup.error_count
        state["stats"] = Stats()
        return state["behaviour"].model_dump()

    @app.get("/_mock/stats")
    async def stats() -> dict:
        return state["stats"].model_dump()

    @app.post("/_mock/hog")
    async def hog(mb: int = 64) -> dict:
        """K8S-02 drill: hold `mb` MiB of memory until the process dies (e.g. OOMKilled)."""
        block = bytearray(mb * 1024 * 1024)
        for i in range(0, len(block), 4096):  # touch every page so it is really resident
            block[i] = 1
        state.setdefault("hog", []).append(block)
        return {"held_mib": sum(len(b) for b in state["hog"]) // (1024 * 1024)}

    def should_fail(b: Behaviour) -> bool:
        if b.error_status is None:
            return False
        if b.error_rate > 0:
            return state["rng"].random() < b.error_rate
        if b.error_count > 0:
            if state["failures_left"] <= 0:
                return False
            state["failures_left"] -= 1
        return True

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        b = current()
        stats: Stats = state["stats"]
        stats.requests += 1
        body = await request.json()
        if body.get("model") not in b.models:
            return JSONResponse(
                {"error": {"message": f"model '{body.get('model')}' not found", "code": None}},
                status_code=404,
            )
        if b.hang:  # never answers; the client must time out. Ends when the client leaves.
            while not await request.is_disconnected():
                await asyncio.sleep(0.05)
            stats.streams_cancelled += 1
            return JSONResponse({}, status_code=499)
        if b.latency_ms:
            await asyncio.sleep(b.latency_ms / 1000)
        if should_fail(b):
            stats.errors_returned += 1
            headers = {"Retry-After": str(b.retry_after)} if b.retry_after is not None else {}
            return JSONResponse(
                {"error": {"message": f"mock error {b.error_status}", "code": b.error_status}},
                status_code=b.error_status,
                headers=headers,
            )
        kind, text = make_reply(body, b)
        stats.by_kind[kind] = stats.by_kind.get(kind, 0) + 1
        prompt_tokens = sum(len(m.get("content") or "") for m in body["messages"]) // 4
        tokens = split_tokens(text)[: max(1, int(body.get("max_tokens") or 10_000))]
        model = body["model"]
        if not body.get("stream"):
            await asyncio.sleep((b.first_token_ms + b.token_ms * len(tokens)) / 1000)
            stats.streams_completed += 1
            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "".join(tokens)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(tokens)},
            }
        return StreamingResponse(
            stream(b, stats, ChunkWriter(b.flavour, model), tokens, prompt_tokens),
            media_type="text/event-stream",
        )

    async def stream(
        b: Behaviour, stats: Stats, writer: ChunkWriter, tokens: list[str], prompt_tokens: int
    ) -> AsyncIterator[bytes]:
        stats.streams_started += 1
        stats.active_streams += 1
        finished = False
        try:
            for part in writer.preamble():
                yield part
            if b.first_token_ms:
                await asyncio.sleep(b.first_token_ms / 1000)
            for i, piece in enumerate(tokens):
                if b.disconnect_after is not None and i >= b.disconnect_after:
                    stats.streams_dropped += 1
                    finished = True  # on purpose, not a client cancellation
                    raise MockDroppedConnection
                if b.malformed_every and i % b.malformed_every == 0:
                    yield writer.event('{"choices": [{"delta": {"content": "unterminated')
                if i and b.token_ms:
                    await asyncio.sleep(b.token_ms / 1000)
                yield writer.content(piece, first=i == 0)
            for part in writer.ending(len(tokens), prompt_tokens):
                yield part
            finished = True
            stats.streams_completed += 1
        finally:
            stats.active_streams -= 1
            if not finished:
                stats.streams_cancelled += 1

    return app


def main() -> None:
    import uvicorn

    port = int(os.environ.get("FXA_MOCK_PORT", "8080"))
    host = os.environ.get("FXA_MOCK_HOST", "0.0.0.0")  # noqa: S104 - container-internal
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
