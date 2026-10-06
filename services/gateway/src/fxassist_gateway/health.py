"""Liveness and readiness (ADR-014).

/healthz: the process is alive and its event loop responds. Nothing else: a liveness probe that
checks dependencies restarts healthy pods when a database blips.

/readyz: can this instance serve requests right now?
  critical (any failure -> 503 "not_ready", traffic is routed elsewhere):
    - qdrant: collection exists and matches the embedding model (DEP-01, DAT-10)
    - llm: a tiny real generation succeeds, re-checked at most every `ready_llm_cache_s`
    - auth: the API key snapshot has been loaded at least once
    - not shutting down (API-08)
  degraded (200 "degraded"; requests still work with a fallback):
    - redis down: cache skipped, in-memory rate limit (CAC-01)
    - postgres down: logs buffered, last key snapshot used (DEP-02)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    critical: bool
    run: Callable[[], Awaitable[str | None]]  # None = ok, otherwise a short reason


class CachedProbe:
    """Runs an expensive probe at most once per `ttl_s`, sharing the result."""

    def __init__(self, probe: Callable[[], Awaitable[None]], ttl_s: float):
        self.probe = probe
        self.ttl_s = ttl_s
        self._at = -float("inf")
        self._result: str | None = "not checked yet"
        self._lock = asyncio.Lock()

    async def __call__(self) -> str | None:
        async with self._lock:
            if time.monotonic() - self._at >= self.ttl_s:
                try:
                    await self.probe()
                    self._result = None
                except Exception as exc:  # report, never raise
                    self._result = f"{type(exc).__name__}: {exc}"[:200]
                self._at = time.monotonic()
            return self._result


class Readiness:
    def __init__(self, checks: list[Check], *, timeout_s: float, draining: Callable[[], bool]):
        self.checks = checks
        self.timeout_s = timeout_s
        self.draining = draining

    async def _run(self, check: Check) -> str | None:
        try:
            return await asyncio.wait_for(check.run(), self.timeout_s)
        except TimeoutError:
            return f"timed out after {self.timeout_s:.0f}s"
        except Exception as exc:  # a broken check must not break the endpoint
            return f"{type(exc).__name__}: {exc}"[:200]

    async def report(self) -> tuple[int, dict[str, Any]]:
        results = await asyncio.gather(*(self._run(c) for c in self.checks))
        checks = {
            c.name: {"ok": r is None, "critical": c.critical, **({"reason": r} if r else {})}
            for c, r in zip(self.checks, results, strict=True)
        }
        if self.draining():
            status = "shutting_down"
        elif any(not v["ok"] and v["critical"] for v in checks.values()):
            status = "not_ready"
        elif any(not v["ok"] for v in checks.values()):
            status = "degraded"
        else:
            status = "ready"
        code = 200 if status in ("ready", "degraded") else 503
        return code, {"status": status, "checks": checks}
