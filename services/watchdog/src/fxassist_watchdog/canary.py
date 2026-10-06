"""The canary: one fixed, tiny prompt to the model server, judged on content and latency."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

PROMPT = "Reply with the single word OK."


@dataclass(frozen=True)
class CanaryResult:
    healthy: bool
    reason: str
    seconds: float


def run_canary(
    base_url: str,
    model: str,
    *,
    timeout_s: float,
    slow_threshold_s: float,
    transport: httpx.BaseTransport | None = None,
) -> CanaryResult:
    started = time.monotonic()
    try:
        with httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport
        ) as c:
            response = c.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": PROMPT}],
                    "max_tokens": 5,
                    "temperature": 0,
                },
            )
    except httpx.TimeoutException:
        return CanaryResult(False, f"no answer within {timeout_s:.0f}s", time.monotonic() - started)
    except httpx.HTTPError as exc:
        return CanaryResult(
            False, f"cannot reach the model ({type(exc).__name__})", time.monotonic() - started
        )
    seconds = time.monotonic() - started
    if response.status_code != 200:
        return CanaryResult(False, f"HTTP {response.status_code}", seconds)
    try:
        text = response.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return CanaryResult(False, "malformed response", seconds)
    if not text.strip():  # sanity check: an empty answer is not a healthy model
        return CanaryResult(False, "empty answer", seconds)
    if seconds > slow_threshold_s:
        return CanaryResult(False, f"too slow ({seconds:.1f}s > {slow_threshold_s:.0f}s)", seconds)
    return CanaryResult(True, f"answered in {seconds:.2f}s", seconds)
