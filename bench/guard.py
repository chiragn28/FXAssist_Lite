"""Benchmark preconditions (ADR-017, CAC-05).

A cached answer costs microseconds, so a benchmark run against a gateway with its cache on
measures Redis, not the model. Every load or benchmark script calls `require_cache_off` first.
"""

from __future__ import annotations

import httpx


class BenchmarkRefused(RuntimeError):
    pass


def require_cache_off(base_url: str, api_key: str, *, timeout_s: float = 5.0) -> dict:
    """Return the gateway's /v1/info, or refuse if its answer cache is enabled."""
    response = httpx.get(
        f"{base_url.rstrip('/')}/v1/info",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout_s,
    )
    response.raise_for_status()
    info = response.json()
    if info.get("cache_enabled") is not False:
        raise BenchmarkRefused(
            "The gateway's answer cache is on, so results would measure cache hits, not the "
            "model. Restart the gateway with FXA_CACHE_ENABLED=false (ADR-017, CAC-05)."
        )
    return info
