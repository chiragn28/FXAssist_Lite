"""The adapter for any OpenAI-compatible chat endpoint (ADR-003).

One code path for every model server (Ollama, vLLM, the mock), used by the CLI, the eval and the
gateway. It always streams internally, because streaming is what lets it:
  - measure time to first token separately from total time (OBS-05)
  - stop early when the caller cancels, closing the connection so the server stops generating
    (LLM-05)
  - tell a stream that broke halfway from one that finished (LLM-02)

Failure handling, in the order a call meets it:
  1. LLM-06: prompt + max_tokens larger than the context window -> rejected before sending
  2. circuit breaker open after repeated failures -> fail fast, no network call (LLM-04)
  3. connect errors and HTTP 429/5xx -> bounded retries, exponential backoff with full jitter,
     honouring Retry-After (LLM-04)
  4. no first token / stalled stream / total time over budget -> LLMTimeoutError (LLM-01)
  5. empty answer -> one retry, then LLMEmptyResponseError (LLM-03)
  6. chunk format differences between servers are normalised; unparseable chunks are skipped
     and counted (LLM-07, LLM-09)

Each error class has a stable `code` that the gateway maps to an HTTP status.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .control import LLMCallStats, RunControl
from .prompts import estimate_tokens

log = logging.getLogger(__name__)


# --- Errors ---------------------------------------------------------------------------------


class LLMError(RuntimeError):
    """The model server could not produce an answer."""

    code = "llm_error"


class LLMUnavailableError(LLMError):
    code = "llm_unavailable"

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class CircuitOpenError(LLMUnavailableError):
    code = "llm_circuit_open"


class ModelNotFoundError(LLMError):
    code = "llm_model_missing"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class LLMEmptyResponseError(LLMError):
    code = "llm_empty"


class LLMStreamError(LLMError):
    """The stream broke after it started, or the server sent something unusable."""

    code = "llm_bad_response"


class LLMContextError(LLMError):
    code = "llm_context_overflow"


class LLMCancelledError(LLMError):
    code = "cancelled"


class _Retryable(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


# --- Protocol the agent depends on ------------------------------------------------------------


class ChatLLM(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> str: ...


# --- Circuit breaker (LLM-04) --------------------------------------------------------------


class CircuitBreaker:
    """Stops calling a server that keeps failing, then lets one trial call through.

    closed --(failure_threshold consecutive failures)--> open
    open   --(reset_timeout_s passed)--> half-open: exactly one trial call is allowed
    half-open --(success)--> closed ; --(failure)--> open again
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False
        self.opened_count = 0

    @property
    def state(self) -> str:
        with self._lock:
            return self._state()

    def _state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_timeout_s:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        with self._lock:
            state = self._state()
            if state == "closed":
                return
            if state == "half_open" and not self._trial_in_flight:
                self._trial_in_flight = True
                return
            opened_at = self._opened_at or self._clock()
            wait = max(0.0, self.reset_timeout_s - (self._clock() - opened_at))
            raise CircuitOpenError(
                "The model server is failing repeatedly; calls are paused (circuit open).",
                retry_after=wait or 1.0,
            )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            was_trial = self._trial_in_flight
            self._trial_in_flight = False
            if was_trial or self._failures >= self.failure_threshold:
                if self._opened_at is None or was_trial:
                    self.opened_count += 1
                    log.warning("LLM circuit opened after %d consecutive failures", self._failures)
                self._opened_at = self._clock()

    def release_trial(self) -> None:
        """A trial call ended without a verdict (e.g. cancelled): let another one through."""
        with self._lock:
            self._trial_in_flight = False


# --- Stream parsing (LLM-07) -------------------------------------------------------------------


@dataclass
class StreamResult:
    text: str = ""
    finished: bool = False  # saw [DONE] or a finish_reason
    finish_reason: str | None = None
    completion_tokens: int | None = None
    prompt_tokens: int | None = None
    malformed_chunks: int = 0
    first_token_at: float | None = None
    parts: list[str] = field(default_factory=list)


def _piece(choice: dict) -> str:
    """Content of one choice, whichever shape the server uses."""
    delta = choice.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return delta["content"]
    message = choice.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    text = choice.get("text")
    return text if isinstance(text, str) else ""


def apply_chunk(obj: object, result: StreamResult, now: float) -> None:
    """Fold one decoded chunk into the result. Raises LLMStreamError for an in-band error."""
    if not isinstance(obj, dict):
        result.malformed_chunks += 1
        return
    if "error" in obj:
        err = obj["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise LLMStreamError(f"model server reported an error mid-stream: {message}")
    choices = obj.get("choices") or []
    for choice in choices if isinstance(choices, list) else []:
        if not isinstance(choice, dict):
            continue
        piece = _piece(choice)
        if piece:
            if result.first_token_at is None:
                result.first_token_at = now
            result.parts.append(piece)
        if choice.get("finish_reason"):
            result.finish_reason = choice["finish_reason"]
            result.finished = True
    usage = obj.get("usage")
    if isinstance(usage, dict):
        result.completion_tokens = usage.get("completion_tokens", result.completion_tokens)
        result.prompt_tokens = usage.get("prompt_tokens", result.prompt_tokens)


def sse_data(lines: Iterator[str]) -> Iterator[str]:
    """Payloads of `data:` lines. Tolerates CRLF, a missing space after the colon, comments
    and `event:` lines. Each data line is one chunk: OpenAI-compatible servers never split a
    chunk over several lines."""
    for line in lines:
        line = line.rstrip("\r")
        if line.startswith("data:"):
            payload = line[5:]
            yield payload[1:] if payload.startswith(" ") else payload


# --- The adapter ---------------------------------------------------------------------------


def _is_local_ollama(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.hostname in {"localhost", "127.0.0.1", "ollama"} and parsed.port == 11434


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str,
        timeout_s: float = 60.0,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 30.0,
        max_retries: int = 2,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 4.0,
        max_context_tokens: int | None = None,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.max_context_tokens = max_context_tokens
        self.breaker = breaker or CircuitBreaker()
        self._clock = clock
        self._rng = rng
        # The read timeout bounds the wait for the first byte and every gap between chunks;
        # the total budget (timeout_s) is checked between chunks.
        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=min(read_timeout_s, timeout_s),
            write=10.0,
            pool=connect_timeout_s,
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def bind(self, control: RunControl) -> BoundLLM:
        """A per-request view that carries cancellation and timing (see control.py)."""
        return BoundLLM(self, control)

    # --- Health -------------------------------------------------------------------------

    def _fix_hint(self) -> str:
        if _is_local_ollama(self.base_url):
            return "Start Ollama with `make up`, then download the model with `make pull-model`."
        return f"Check that the server at {self.base_url} is running and serves {self.model}."

    def check_model(self) -> None:
        """ENV-03: fail early with the exact fix instead of a stack trace mid-answer."""
        try:
            response = self._client.get("/models")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Cannot reach the LLM server at {self.base_url} ({type(exc).__name__}). "
                + self._fix_hint()
            ) from exc
        ids = {m.get("id") for m in response.json().get("data", [])}
        if self.model not in ids:
            raise ModelNotFoundError(
                f"Model '{self.model}' is not available at {self.base_url} "
                f"(available: {sorted(i for i in ids if i) or 'none'}). " + self._fix_hint()
            )

    def probe(self, timeout_s: float = 10.0) -> float:
        """A tiny real generation for readiness checks (ADR-014). Returns seconds taken.

        Bypasses retries and the circuit breaker: it reports health, it does not serve users.
        """
        started = self._clock()
        body = self._body([{"role": "user", "content": "Reply with OK."}], 2, 0.0, False)
        timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, self._timeout.connect or 5.0))
        result = self._stream_once(body, started + timeout_s, None, timeout=timeout)
        if not result.finished:
            raise LLMStreamError("probe stream ended without finishing")
        return self._clock() - started

    # --- Completion -----------------------------------------------------------------------

    def _body(self, messages, max_tokens, temperature, json_mode) -> dict:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": 0,
            "stream": True,
            "stream_options": {"include_usage": True},  # token counts for tokens/s (OBS)
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def check_length(self, messages: list[dict[str, str]], max_tokens: int) -> None:
        """LLM-06: refuse to send a request that cannot fit, with the exact numbers."""
        if not self.max_context_tokens:
            return
        prompt = sum(estimate_tokens(m.get("content") or "") + 4 for m in messages)
        if prompt + max_tokens > self.max_context_tokens:
            raise LLMContextError(
                f"Prompt is about {prompt} tokens and {max_tokens} output tokens were requested: "
                f"{prompt + max_tokens} > the model's context window of "
                f"{self.max_context_tokens} tokens. Shorten the input or lower max_tokens."
            )

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        json_mode: bool = False,
        control: RunControl | None = None,
    ) -> str:
        self.check_length(messages, max_tokens)
        body = self._body(messages, max_tokens, temperature, json_mode)
        started = self._clock()
        deadline = started + self.timeout_s
        attempts = 0
        empty_retry_used = False
        stats = LLMCallStats(kind="json" if json_mode else "text", started=started)
        if control is not None:
            control.llm_calls.append(stats)
        while True:
            if control is not None and control.cancelled():
                raise LLMCancelledError("request cancelled")
            self.breaker.before_call()
            attempts += 1
            stats.attempts = attempts
            try:
                result = self._stream_once(body, deadline, control)
            except _Retryable as exc:
                self.breaker.record_failure()
                delay = self._backoff(attempts, exc.retry_after)
                if attempts > self.max_retries or self._clock() + delay >= deadline:
                    stats.error = LLMUnavailableError.code
                    raise LLMUnavailableError(
                        f"{exc} (gave up after {attempts} attempt(s)). " + self._fix_hint(),
                        retry_after=exc.retry_after,
                    ) from exc
                log.warning("LLM call failed (%s); retry %d in %.2fs", exc, attempts, delay)
                self._sleep(delay, control)
                continue
            except LLMCancelledError:
                self.breaker.release_trial()
                stats.error = LLMCancelledError.code
                raise
            except (LLMTimeoutError, LLMStreamError) as exc:
                self.breaker.record_failure()
                stats.error = exc.code
                raise
            except LLMError as exc:
                self.breaker.release_trial()
                stats.error = exc.code
                raise
            # The server answered properly: it is healthy, even if the answer is empty.
            self.breaker.record_success()
            text = "".join(result.parts)
            stats.malformed_chunks += result.malformed_chunks
            if result.first_token_at is not None and stats.ttft_s is None:
                stats.ttft_s = result.first_token_at - started
            if not text.strip():
                if not empty_retry_used:  # LLM-03: one bounded retry
                    empty_retry_used = True
                    log.warning("LLM returned an empty answer; retrying once")
                    continue
                stats.error = LLMEmptyResponseError.code
                raise LLMEmptyResponseError("The model returned an empty answer twice.")
            stats.total_s = self._clock() - started
            stats.output_chars = len(text)
            stats.completion_tokens = result.completion_tokens
            stats.prompt_tokens = result.prompt_tokens
            return text

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        """Exponential backoff with full jitter; Retry-After wins when the server sends it."""
        if retry_after is not None:
            return min(retry_after, self.backoff_max_s)
        ceiling = min(self.backoff_max_s, self.backoff_base_s * 2 ** (attempt - 1))
        return self._rng() * ceiling

    def _sleep(self, seconds: float, control: RunControl | None) -> None:
        if control is not None:
            if control.cancel_event.wait(seconds):
                raise LLMCancelledError("request cancelled")
        else:
            time.sleep(seconds)

    def _stream_once(
        self,
        body: dict,
        deadline: float,
        control: RunControl | None,
        timeout: httpx.Timeout | None = None,
    ) -> StreamResult:
        timeout = timeout or self._timeout
        result = StreamResult()
        try:
            with self._client.stream(
                "POST", "/chat/completions", json=body, timeout=timeout
            ) as response:
                if response.status_code in RETRYABLE_STATUS:
                    raise _Retryable(
                        f"LLM server returned HTTP {response.status_code}",
                        _retry_after_seconds(response.headers.get("retry-after")),
                    )
                if response.status_code == 404:
                    raise ModelNotFoundError(
                        f"Model '{self.model}' was not found at {self.base_url}. "
                        + self._fix_hint()
                    )
                if response.status_code != 200:
                    raise LLMStreamError(f"LLM server returned HTTP {response.status_code}")
                ctype = response.headers.get("content-type", "")
                if "text/event-stream" not in ctype:
                    # Some servers ignore stream=true and send one JSON body (LLM-07).
                    response.read()
                    try:
                        apply_chunk(response.json(), result, self._clock())
                    except ValueError as exc:
                        raise LLMStreamError("LLM server returned a malformed response") from exc
                    result.finished = True
                    return result
                for data in sse_data(response.iter_lines()):
                    if control is not None and control.cancelled():
                        raise LLMCancelledError("request cancelled")  # closes the connection
                    now = self._clock()
                    if now > deadline:
                        raise LLMTimeoutError(
                            f"The model did not finish within {self.timeout_s:.0f}s."
                        )
                    if data.strip() == "[DONE]":
                        result.finished = True
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        result.malformed_chunks += 1  # LLM-07/09: skip it, keep going
                        continue
                    apply_chunk(obj, result, now)
        except httpx.ConnectError as exc:
            raise _Retryable(f"Cannot connect to the LLM server ({type(exc).__name__})") from exc
        except httpx.ConnectTimeout as exc:
            raise _Retryable("Connecting to the LLM server timed out") from exc
        except httpx.PoolTimeout as exc:
            raise _Retryable("No free connection to the LLM server") from exc
        except httpx.TimeoutException as exc:
            waited = "first token" if result.first_token_at is None else "next token"
            raise LLMTimeoutError(
                f"The model server sent nothing for {timeout.read:.0f}s "
                f"while waiting for the {waited}."
            ) from exc
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            if not result.parts:
                raise _Retryable(f"Connection to the LLM server dropped ({exc})") from exc
            raise LLMStreamError(
                f"The model's stream broke after {len(result.parts)} chunk(s)."
            ) from exc
        if not result.finished:
            raise LLMStreamError("The model's stream ended without finishing.")
        return result


class BoundLLM:
    """OpenAICompatLLM bound to one request's RunControl; satisfies ChatLLM."""

    def __init__(self, llm: OpenAICompatLLM, control: RunControl):
        self._llm = llm
        self.control = control
        self.model = llm.model

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> str:
        return self._llm.complete(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            control=self.control,
        )
