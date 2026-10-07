"""The FastAPI gateway (Phase 2).

POST /v1/ask, in order:
  1. request ID (API-06); rejected with 503 while shutting down (API-08)
  2. API key from the in-memory snapshot; 401 with no detail (API-01)
  3. body: size cap 413, UTF-8 JSON and question rules 422 (API-03)
  4. token bucket per key; 429 + Retry-After (API-02)
  5. corpus version from Qdrant; 503 if unreachable or not ingested (DEP-01)
  6. answer cache (ADR-010), then coalescing with identical in-flight requests (API-04)
  7. the agent in a worker thread, with a bounded queue (503 "busy")
  8. answer + citations + the gateway's disclaimer (SAF-06), as SSE (default) or JSON

Streaming sends progress (`status`), then the validated answer (`answer`), then `done`; or an
`error` event. Answer text is released only after the citation and number checks (ADR-024).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from fxassist_agent.control import RunControl
from fxassist_agent.embeddings import Embedder
from fxassist_agent.graph import Agent, AgentDeps
from fxassist_agent.llm import OpenAICompatLLM
from fxassist_agent.prompts import PROMPT_VERSION
from fxassist_agent.store import StoreError, VectorStore
from fxassist_agent.tracing import content_for_span

from . import metrics
from .answers import cache_key, payload, ttl_for
from .auth import Authenticator, KeyStore, presented_key
from .config import GatewaySettings
from .errors import ApiError, from_agent_error
from .health import CachedProbe, Check, Readiness
from .logwriter import LogStore, RequestLogWriter
from .middleware import RequestContextMiddleware, RequestIdFilter
from .ocr import OcrError, TesseractOcr, clean, prepare
from .redis_state import AnswerCache, RateLimiter, RedisGuard
from .runner import AgentRunner, Flight, Flights, RunOutcome
from .sse import KEEPALIVE, EventStreamResponse, event

log = logging.getLogger(__name__)
tracer = trace.get_tracer("fxassist.gateway")


# --- Request model (API-03) -----------------------------------------------------------------


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    stream: bool = True

    @field_validator("question")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be empty")
        if "\x00" in value:
            raise ValueError("question contains a NUL character")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:  # lone surrogates sent as \ud800 escapes
            raise ValueError("question is not valid Unicode text") from None
        return value


async def read_body(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it is larger than `limit` (API-03)."""
    too_large = ApiError(413, "payload_too_large", f"Request body is larger than {limit} bytes.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise too_large
    return bytes(body)


async def read_ask_request(request: Request, settings: GatewaySettings) -> AskRequest:
    body = await read_body(request, settings.max_body_bytes)
    try:
        data = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError:
        raise ApiError(422, "invalid_encoding", "Request body must be UTF-8 JSON.") from None
    except ValueError:
        raise ApiError(422, "invalid_json", "Request body must be a JSON object.") from None
    try:
        ask = AskRequest.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "body"
        # Only the location and the rule: never echo the input back.
        raise ApiError(422, "invalid_request", f"{where}: {first['msg']}") from None
    if len(ask.question) > settings.max_question_chars:
        raise ApiError(
            422,
            "question_too_long",
            f"Question is {len(ask.question)} characters; the limit is "
            f"{settings.max_question_chars}.",
        )
    return ask


# --- Components -------------------------------------------------------------------------------


class IndexInfo:
    """The corpus version (part of the cache key), re-read from Qdrant at most every `ttl_s`.

    Also checks the collection matches the embedding model (DAT-10). Any failure is a 503:
    without the documents there is no honest answer to give (DEP-01).
    """

    def __init__(self, store: VectorStore, ttl_s: float, timeout_s: float = 3.0):
        self.store = store
        self.ttl_s = ttl_s
        self.timeout_s = timeout_s
        self._value: str | None = None
        self._at = -float("inf")

    def _read(self) -> str:
        self.store.check_compatible()
        return self.store.corpus_version() or "unversioned"

    async def corpus_version(self, *, fresh: bool = False) -> str:
        if not fresh and self._value is not None and time.monotonic() - self._at < self.ttl_s:
            return self._value
        try:
            value = await asyncio.wait_for(asyncio.to_thread(self._read), self.timeout_s)
        except StoreError as exc:  # collection missing or built with another model
            metrics.errors.add(1, {"code": "index_not_ready"})
            raise ApiError(503, "index_not_ready", str(exc), retry_after=30) from exc
        except Exception as exc:  # Qdrant unreachable or timing out
            metrics.dependency_failures.add(1, {"dependency": "qdrant"})
            raise ApiError(
                503,
                "store_unavailable",
                "The document index is unavailable. Please try again shortly.",
                retry_after=5,
            ) from exc
        self._value, self._at = value, time.monotonic()
        return value


@dataclass
class Components:
    settings: GatewaySettings
    auth: Authenticator
    redis: RedisGuard
    limiter: RateLimiter
    cache: AnswerCache
    store: VectorStore
    embedder: Embedder
    llm: OpenAICompatLLM
    index: IndexInfo
    runner: AgentRunner
    flights: Flights
    logs: RequestLogWriter
    postgres_ping: Callable[[], Awaitable[bool]]
    background: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def make_agent(self, control: RunControl) -> Agent:
        return Agent(
            AgentDeps(
                settings=self.settings,
                store=self.store,
                embedder=self.embedder,
                llm=self.llm.bind(control),
                control=control,
            )
        )


async def assemble(
    settings: GatewaySettings,
    *,
    redis_client,
    key_store: KeyStore,
    log_store: LogStore,
    store: VectorStore,
    embedder: Embedder,
    llm: OpenAICompatLLM,
    postgres_ping: Callable[[], Awaitable[bool]],
) -> Components:
    """Wire the components from their parts. Tests call this with in-memory parts."""
    guard = RedisGuard(redis_client, settings.redis_retry_after_s)
    holder: dict[str, Components] = {}
    runner = AgentRunner(
        lambda control: holder["c"].make_agent(control),
        max_concurrent=settings.max_concurrent_runs,
        queue_timeout_s=settings.queue_timeout_s,
    )
    auth = Authenticator(key_store, settings.key_refresh_s)
    await auth.refresh()  # best effort; the background task keeps retrying (DEP-04)
    logs = RequestLogWriter(
        log_store, max_queue=settings.log_queue_max, batch_size=settings.log_batch_size
    )
    components = Components(
        settings=settings,
        auth=auth,
        redis=guard,
        limiter=RateLimiter(
            guard,
            per_minute=settings.rate_limit_per_minute,
            burst=settings.rate_limit_burst,
            fallback_divisor=settings.rate_limit_fallback_divisor,
        ),
        cache=AnswerCache(guard, enabled=settings.cache_enabled),
        store=store,
        embedder=embedder,
        llm=llm,
        index=IndexInfo(store, settings.index_info_ttl_s),
        runner=runner,
        flights=Flights(),
        logs=logs,
        postgres_ping=postgres_ping,
        background=[auth.run, logs.run],
    )
    holder["c"] = components
    return components


async def build_components(settings: GatewaySettings) -> Components:
    """The real thing: Redis, PostgreSQL, Qdrant, fastembed and the configured LLM server.

    Nothing here waits for a dependency to be up, so the gateway starts in any order and
    reports what is missing through /readyz (DEP-04).
    """
    from qdrant_client import QdrantClient
    from redis.asyncio import Redis
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff

    from fxassist_agent.cli import build_embedder, build_llm

    from .db import PostgresStore

    redis_client = Redis.from_url(
        settings.resolved_redis_url,
        socket_timeout=settings.redis_timeout_s,
        socket_connect_timeout=settings.redis_timeout_s,
        retry=Retry(NoBackoff(), 0),  # redis-py retries 3x by default; RedisGuard decides
    )
    postgres = PostgresStore(settings.postgres_conninfo)
    await postgres.open()
    embedder = build_embedder(settings)
    qdrant = QdrantClient(url=settings.resolved_qdrant_url, timeout=5, check_compatibility=False)
    components = await assemble(
        settings,
        redis_client=redis_client,
        key_store=postgres,
        log_store=postgres,
        store=VectorStore(qdrant, settings.collection, embedder),
        embedder=embedder,
        llm=build_llm(settings),
        postgres_ping=postgres.ping,
    )

    async def close() -> None:
        await redis_client.aclose()
        await postgres.close()
        components.llm.close()
        qdrant.close()

    components.closers.append(close)
    from .telemetry import register_gauges

    register_gauges(components)
    return components


# --- Responses and logging --------------------------------------------------------------------


class AgentFailed(Exception):
    """The agent ran but could not answer; carries the run for logging."""

    def __init__(self, error: ApiError, run: RunOutcome):
        super().__init__(error.message)
        self.error = error
        self.run = run


@dataclass
class Computed:
    body: dict[str, Any]
    run: RunOutcome


def run_timings(run: RunOutcome) -> dict[str, Any]:
    """OBS-05: queue, retrieval, time to first token and LLM time, kept separate."""
    steps = run.result.steps
    calls = run.control.llm_calls
    generation = [c for c in calls if c.kind == "text"]
    ttft = generation[-1].ttft_s if generation else None
    return {
        "queue_ms": round(run.queue_s * 1000),
        "retrieval_ms": round(sum(s["seconds"] for s in steps if s["node"] == "retrieve") * 1000),
        "ttft_ms": round(ttft * 1000) if ttft is not None else None,
        "llm_ms": round(sum(c.total_s or 0.0 for c in calls) * 1000),
        "completion_tokens": sum(c.completion_tokens or 0 for c in calls) or None,
        "truncated": run.result.truncated,
    }


def record_run_metrics(run: RunOutcome) -> None:
    """OBS-05: each stage recorded on its own, so a slow total can be attributed."""
    metrics.stage_duration.record(run.queue_s, {"stage": "queue"})
    for step in run.result.steps:
        if step["node"] == "retrieve" and not step.get("skipped"):
            metrics.stage_duration.record(step["seconds"], {"stage": "retrieval"})
    llm_total = 0.0
    for call in run.control.llm_calls:
        metrics.llm_calls.add(1, {"kind": call.kind, "result": call.error or "ok"})
        if call.malformed_chunks:
            metrics.llm_malformed_chunks.add(call.malformed_chunks)
        llm_total += call.total_s or 0.0
        if call.completion_tokens:
            metrics.llm_output_tokens.add(call.completion_tokens, {"kind": call.kind})
        if call.kind != "text" or call.error is not None:
            continue
        if call.ttft_s is not None:
            metrics.stage_duration.record(call.ttft_s, {"stage": "ttft"})
        generating = (call.total_s or 0.0) - (call.ttft_s or 0.0)
        if call.completion_tokens and call.completion_tokens > 1 and generating > 0:
            # Decode speed: tokens after the first, over the time after the first token.
            metrics.llm_tokens_per_second.record(
                (call.completion_tokens - 1) / generating, {"kind": call.kind}
            )
    if run.control.llm_calls:
        metrics.stage_duration.record(llm_total, {"stage": "llm"})
    if run.result.truncated:
        metrics.truncations.add(run.result.truncated)


def response_body(
    body: dict[str, Any], *, request_id: str, cached: bool, coalesced: bool
) -> dict[str, Any]:
    return {"request_id": request_id, **body, "cached": cached, "coalesced": coalesced}


class RequestRecord:
    """Everything known about one request, written to the request log at the end."""

    def __init__(
        self,
        request_id: str,
        route: str,
        settings: GatewaySettings,
        span_name: str = "fxassist.ask",
    ):
        self.started = time.monotonic()
        self.settings = settings
        self.fields: dict[str, Any] = {
            "request_id": request_id,
            "route": route,
            "status": 500,
            "cached": False,
            "coalesced": False,
            "streamed": False,
        }
        # The request's span. Its context is handed to the agent run explicitly, because the
        # run happens in another task and then a worker thread.
        # FastAPI (0.142+) already emits its own HTTP server span; this one carries the
        # FXAssist view of the request (outcome, cache, coalescing) and parents the agent spans.
        self.span = tracer.start_span(span_name, kind=SpanKind.INTERNAL)
        self.span.set_attribute("fxa.request_id", request_id)
        self.trace_context = trace.set_span_in_context(self.span)
        self._finished = False

    def question(self, text: str) -> None:
        self.fields["question_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        self.fields["question_chars"] = len(text)
        self.span.set_attribute("fxa.question_chars", len(text))
        if self.settings.log_questions:  # OBS-02: off by default
            self.fields["question_text"] = text[:500]
        if self.settings.trace_content:
            self.span.set_attribute("input.value", content_for_span(text))

    def error(self, err: ApiError) -> None:
        self.fields.update(status=err.status, error_code=err.code)
        metrics.errors.add(1, {"code": err.code})

    def finish(self, logs: RequestLogWriter) -> None:
        from .db import now_utc

        if self._finished:
            return
        self._finished = True
        seconds = time.monotonic() - self.started
        self.fields["total_ms"] = round(seconds * 1000)
        self.fields["ts"] = now_utc()
        outcome = self.fields.get("outcome") or "none"
        cached = str(bool(self.fields["cached"])).lower()
        metrics.requests.add(
            1,
            {
                "route": self.fields["route"],
                "status": str(self.fields["status"]),
                "outcome": outcome,
            },
        )
        if self.fields["status"] == 200:
            metrics.request_duration.record(seconds, {"outcome": outcome, "cached": cached})
        logs.submit(dict(self.fields))
        status = self.fields["status"]
        self.span.set_attribute("http.response.status_code", status)
        self.span.set_attribute("fxa.outcome", outcome)
        self.span.set_attribute("fxa.cached", self.fields["cached"])
        self.span.set_attribute("fxa.coalesced", self.fields["coalesced"])
        if self.fields.get("error_code"):
            self.span.set_attribute("error.type", self.fields["error_code"])
        if status >= 500:
            self.span.set_status(Status(StatusCode.ERROR, self.fields.get("error_code")))
        self.span.end()


# --- The app ------------------------------------------------------------------------------------


def create_app(
    settings: GatewaySettings | None = None,
    components_factory: Callable[[GatewaySettings], Awaitable[Components]] | None = None,
) -> FastAPI:
    settings = settings or GatewaySettings()
    factory = components_factory or build_components

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.debug_startup_delay_s:  # K8S-01 drill: a slow start must not be killed
            log.warning("debug: delaying startup by %.0fs", settings.debug_startup_delay_s)
            await asyncio.sleep(settings.debug_startup_delay_s)
        components = await factory(settings)
        app.state.c = components
        tasks = [asyncio.create_task(job()) for job in components.background]
        try:
            yield
        finally:
            # API-08: uvicorn has already stopped accepting and waited for in-flight requests.
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            await components.logs.flush(timeout_s=5.0)
            components.runner.shutdown()
            for close in components.closers:
                await close()

    app = FastAPI(title="FXAssist Lite", version="0.2.0", lifespan=lifespan)
    app.state.draining = False
    app.add_middleware(RequestContextMiddleware, draining=lambda: app.state.draining)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            exc.body(request.scope.get("state", {}).get("request_id")),
            status_code=exc.status,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        err = ApiError(500, "internal_error", "Something went wrong. Please try again.")
        return JSONResponse(
            err.body(request.scope.get("state", {}).get("request_id")), status_code=500
        )

    if settings.ui_enabled:
        add_ui_routes(app)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        code, body = await readiness(request.app).report()
        return JSONResponse(body, status_code=code)

    def readiness(app: FastAPI) -> Readiness:
        if not hasattr(app.state, "readiness"):
            c: Components = app.state.c

            async def qdrant() -> str | None:
                await c.index.corpus_version(fresh=True)
                return None

            async def llm_probe() -> None:
                await asyncio.to_thread(c.llm.probe, settings.ready_timeout_s)

            llm_check = CachedProbe(llm_probe, settings.ready_llm_cache_s)

            async def llm() -> str | None:
                if c.llm.breaker.state == "open":
                    return "circuit open after repeated failures"
                return await llm_check()

            async def auth() -> str | None:
                return None if c.auth.ready else "API keys not loaded yet"

            async def redis() -> str | None:
                return None if await c.redis.ping() else "unreachable: cache off, local limits"

            async def postgres() -> str | None:
                ok = await c.postgres_ping()
                return None if ok else "unreachable: logs buffered, cached keys in use"

            app.state.readiness = Readiness(
                [
                    Check("qdrant", True, qdrant),
                    Check("llm", True, llm),
                    Check("auth", True, auth),
                    Check("redis", False, redis),
                    Check("postgres", False, postgres),
                ],
                timeout_s=settings.ready_timeout_s,
                draining=lambda: app.state.draining,
            )
        return app.state.readiness

    @app.get("/v1/info")
    async def info(request: Request) -> dict:
        """Configuration a client may need, e.g. the benchmark guard checks the cache (CAC-05)."""
        c: Components = request.app.state.c
        c.auth.verify(presented_key(request.headers))
        return {
            "model": settings.llm_model,
            "cache_enabled": c.cache.enabled,
            "prompt_version": PROMPT_VERSION,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
        }

    ocr_engine = TesseractOcr(binary=settings.ocr_command, timeout_s=settings.ocr_timeout_s)
    ocr_slots = asyncio.Semaphore(settings.ocr_concurrency)

    @app.post("/v1/ocr")
    async def ocr(request: Request) -> dict:
        """ADR-027: the text in an image (raw PNG, JPEG or WebP body), for the web page."""
        c: Components = request.app.state.c
        request_id: str = request.scope["state"]["request_id"]
        record = RequestRecord(request_id, "/v1/ocr", settings, span_name="fxassist.ocr")
        try:
            key = c.auth.verify(presented_key(request.headers))
            record.fields["key_id"] = key.key_id
            if not settings.ocr_enabled or not ocr_engine.available():
                raise ApiError(503, "ocr_unavailable", "Reading images is not available here.")
            decision = await c.limiter.check(key.key_id)  # an image counts like a question
            if not decision.allowed:
                raise ApiError(
                    429,
                    "rate_limited",
                    "Too many requests for this API key.",
                    retry_after=decision.retry_after,
                )
            data = await read_body(request, settings.ocr_max_bytes)
            try:
                png = prepare(
                    data,
                    request.headers.get("content-type", ""),
                    max_pixels=settings.ocr_max_pixels,
                )
                async with ocr_slots:
                    raw = await asyncio.to_thread(ocr_engine.read, png)
            except OcrError as err:
                status = {"unsupported_media_type": 415, "ocr_timeout": 503}.get(err.code, 422)
                raise ApiError(status, err.code, err.message) from None
            text, truncated = clean(raw, max_chars=settings.ocr_max_chars)
            record.fields.update(status=200, outcome="text" if text else "no_text")
            return {"request_id": request_id, "text": text, "truncated": truncated}
        except ApiError as err:
            record.error(err)
            raise
        finally:
            record.finish(c.logs)

    @app.post("/v1/ask")
    async def ask(request: Request):
        c: Components = request.app.state.c
        request_id: str = request.scope["state"]["request_id"]
        record = RequestRecord(request_id, "/v1/ask", settings)
        streaming = False
        try:
            key = c.auth.verify(presented_key(request.headers))
            record.fields["key_id"] = key.key_id
            ask_request = await read_ask_request(request, settings)
            record.question(ask_request.question)
            decision = await c.limiter.check(key.key_id)
            if not decision.allowed:
                raise ApiError(
                    429,
                    "rate_limited",
                    "Too many requests for this API key.",
                    retry_after=decision.retry_after,
                )
            corpus_version = await c.index.corpus_version()
            record.fields.update(corpus_version=corpus_version, model=settings.llm_model)
            key_ = cache_key(
                ask_request.question, corpus_version, PROMPT_VERSION, settings.model_id
            )
            cached = await c.cache.get(key_)
            compute = make_compute(
                c, ask_request.question, key_, corpus_version, record.trace_context
            )
            if ask_request.stream:
                streaming = True
                record.fields["streamed"] = True
                return stream_response(c, record, request_id, cached, key_, compute)
            if cached is not None:
                record.fields.update(cached=True, status=200, outcome=cached.get("outcome"))
                return response_body(cached, request_id=request_id, cached=True, coalesced=False)
            async with c.flights.join(key_, compute) as (flight, shared):
                record.fields["coalesced"] = shared
                computed = await asyncio.shield(flight.task)
            record.fields.update(status=200, outcome=computed.body["outcome"])
            record.fields.update(run_timings(computed.run))
            return response_body(
                computed.body, request_id=request_id, cached=False, coalesced=shared
            )
        except AgentFailed as failed:
            record.fields.update(outcome="error", **run_timings(failed.run))
            record.error(failed.error)
            raise failed.error from None
        except ApiError as err:
            record.error(err)
            raise
        finally:
            if not streaming:
                record.finish(c.logs)

    return app


# --- The web page (ADR-027) ------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
UI_FILES = {"app.js": "text/javascript", "style.css": "text/css"}
# The page loads only its own files and talks only to this gateway. Model text is inserted with
# textContent, never as HTML; the policy is a second line of defence (SAF-09).
UI_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


def add_ui_routes(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return Response(
            (STATIC_DIR / "index.html").read_bytes(),
            media_type="text/html; charset=utf-8",
            headers=UI_HEADERS,
        )

    @app.get("/ui/{name}", include_in_schema=False)
    async def ui_file(name: str) -> Response:
        if name not in UI_FILES:  # a fixed list: no paths from the URL reach the filesystem
            raise ApiError(404, "not_found", "Not found.")
        return Response(
            (STATIC_DIR / name).read_bytes(),
            media_type=f"{UI_FILES[name]}; charset=utf-8",
            headers=UI_HEADERS,
        )


def make_compute(
    c: Components, question: str, key: str, corpus_version: str, trace_context
) -> Callable[[Flight], Awaitable[Computed]]:
    async def compute(flight: Flight) -> Computed:
        flight.publish("queued")
        token = otel_context.attach(trace_context)  # agent spans become children of the request
        try:
            run = await c.runner.run(question, flight.publish)
        finally:
            otel_context.detach(token)
        record_run_metrics(run)
        result = run.result
        if result.outcome == "error":
            retry_after = None
            if result.error_code == "llm_circuit_open":
                retry_after = c.llm.breaker.reset_timeout_s
            raise AgentFailed(from_agent_error(result.error_code, retry_after), run)
        body = payload(result, corpus_version=corpus_version, model=c.settings.llm_model)
        ttl = ttl_for(
            result.outcome, normal_s=c.settings.cache_ttl_s, short_s=c.settings.cache_short_ttl_s
        )
        await c.cache.set(key, body, ttl)  # CAC-03: only reached for non-error outcomes
        return Computed(body, run)

    return compute


def stream_response(
    c: Components,
    record: RequestRecord,
    request_id: str,
    cached: dict[str, Any] | None,
    key: str,
    compute: Callable[[Flight], Awaitable[Computed]],
) -> EventStreamResponse:
    def error_event(err: ApiError) -> bytes:
        record.error(err)
        return event(
            "error",
            {
                "code": err.code,
                "message": err.message,
                "status": err.status,
                "request_id": request_id,
            },
        )

    async def events() -> AsyncIterator[bytes]:
        yield event("meta", {"request_id": request_id, "cached": cached is not None})
        if cached is not None:
            record.fields.update(cached=True, status=200, outcome=cached.get("outcome"))
            body = response_body(cached, request_id=request_id, cached=True, coalesced=False)
            yield event("answer", body)
            yield event("done", {})
            return
        async with c.flights.join(key, compute) as (flight, shared):
            record.fields["coalesced"] = shared
            stages = flight.listen()
            getter: asyncio.Future | None = None
            try:
                while True:
                    getter = asyncio.ensure_future(stages.get())
                    done, _ = await asyncio.wait(
                        {getter, flight.task},
                        timeout=c.settings.sse_keepalive_s,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if getter in done:
                        yield event("status", {"stage": getter.result()})
                    else:
                        getter.cancel()
                    if flight.task in done:
                        break
                    if not done:
                        yield KEEPALIVE  # API-05: keeps idle-timeout proxies from closing
            finally:
                if getter is not None:
                    getter.cancel()
            try:
                computed: Computed = flight.task.result()
            except AgentFailed as failed:
                record.fields.update(outcome="error", **run_timings(failed.run))
                yield error_event(failed.error)  # LLM-02: an explicit error event, never cached
                return
            except ApiError as err:  # e.g. busy
                yield error_event(err)
                return
            except asyncio.CancelledError:
                yield error_event(ApiError(503, "cancelled", "The request was cancelled."))
                return
            except Exception:
                log.exception("unhandled error in agent run")
                yield error_event(ApiError(500, "internal_error", "Something went wrong."))
                return
        record.fields.update(status=200, outcome=computed.body["outcome"])
        record.fields.update(run_timings(computed.run))
        body = response_body(computed.body, request_id=request_id, cached=False, coalesced=shared)
        yield event("answer", body)
        yield event("done", {})

    def finished(reason: str) -> None:
        if reason != "completed":
            record.fields.setdefault("error_code", reason)
            if reason == "client_disconnected":
                record.fields["status"] = 499  # nginx's convention: client closed request
        record.finish(c.logs)

    return EventStreamResponse(
        events(), write_timeout_s=c.settings.sse_write_timeout_s, on_finish=finished
    )


def configure_logging(level: str = "INFO") -> None:
    from .telemetry import RedactingFilter

    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactingFilter())  # OBS-02: no keys or passwords in any log line
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Qdrant/LLM call is noise
