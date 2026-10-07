"""Load generator and measurement for one OpenAI-compatible server (ADR-017).

One *cell* = one load shape (concurrency x prompt set) against one server configuration,
repeated `repetitions` times. For each repetition:
  1. warm-up requests are sent and their results discarded (BEN-01)
  2. `requests` measured requests are sent with `concurrency` in flight at once
  3. every request streams; time to first token (TTFT), total time and generated tokens are
     taken from the stream itself, and output length is fixed with `max_tokens` plus
     `ignore_eos` (vLLM), so tokens per second compares like with like (BEN-08)
  4. failed requests are counted, and left out of latency statistics (BEN-06)
  5. one summary row is appended to disk and fsync'd before the next repetition (GPU-05)

Everything that defines the measurement (server config, prompt set hash, max tokens, prefix
caching, cache state) is written into every row (BEN-02, BEN-04), so rows can be compared
without trusting anyone's memory.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import os
import platform
import statistics
import time
from collections.abc import Coroutine
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx

from .prompts import PromptSet

T = TypeVar("T")


@dataclass(frozen=True)
class Cell:
    experiment: str  # e.g. "baseline", "prefix-caching-on"
    variant: str  # model variant, e.g. "fp16" or "awq"
    concurrency: int
    prompt_set: str  # "short" or "long"
    max_tokens: int = 128
    requests: int = 32  # measured requests per repetition
    warmup: int = 4
    repetitions: int = 3  # BEN-03

    @property
    def key(self) -> str:
        return f"{self.experiment}|{self.variant}|c{self.concurrency}|{self.prompt_set}"


@dataclass
class RequestResult:
    ok: bool
    ttft_s: float | None = None
    total_s: float | None = None
    output_tokens: int = 0
    error: str | None = None


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for no data (never a made-up zero)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))  # smallest value with q% at or below it
    return ordered[rank - 1]


async def one_request(
    client: httpx.AsyncClient, model: str, prompt: str, max_tokens: int, seed: int
) -> RequestResult:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "seed": seed,
        "stream": True,
        "stream_options": {"include_usage": True},
        "ignore_eos": True,  # vLLM: always generate max_tokens (BEN-08); others ignore it
    }
    started = time.perf_counter()
    first: float | None = None
    chunks = 0
    usage_tokens: int | None = None
    try:
        async with client.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code != 200:
                await response.aread()
                return RequestResult(False, error=f"HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                for choice in obj.get("choices") or []:
                    if (choice.get("delta") or {}).get("content"):
                        chunks += 1
                        if first is None:
                            first = time.perf_counter()
                if obj.get("usage"):
                    usage_tokens = obj["usage"].get("completion_tokens")
    except httpx.HTTPError as exc:
        return RequestResult(False, error=type(exc).__name__)
    total = time.perf_counter() - started
    if first is None:
        return RequestResult(False, total_s=total, error="no content")
    return RequestResult(
        True, ttft_s=first - started, total_s=total, output_tokens=usage_tokens or chunks
    )


async def run_requests(
    base_url: str,
    model: str,
    prompts: list[str],
    n: int,
    concurrency: int,
    max_tokens: int,
    seed_offset: int,
    timeout_s: float,
) -> tuple[list[RequestResult], float]:
    """Send n requests, `concurrency` at a time. Returns results and wall-clock seconds."""
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s, limits=limits) as client:
        queue: asyncio.Queue[int] = asyncio.Queue()
        for i in range(n):
            queue.put_nowait(i)
        results: list[RequestResult] = []

        async def worker() -> None:
            while True:
                try:
                    i = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                prompt = prompts[i % len(prompts)]
                results.append(
                    await one_request(client, model, prompt, max_tokens, seed_offset + i)
                )

        started = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        return results, time.perf_counter() - started


def summarise(results: list[RequestResult], wall_s: float) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    latencies = [r.total_s for r in ok]
    ttfts = [r.ttft_s for r in ok]
    per_request_tps = [
        (r.output_tokens - 1) / (r.total_s - r.ttft_s)
        for r in ok
        if r.output_tokens > 1 and r.total_s > r.ttft_s
    ]
    errors: dict[str, int] = {}
    for r in results:
        if not r.ok:
            errors[r.error or "unknown"] = errors.get(r.error or "unknown", 0) + 1
    return {
        "requests": len(results),
        "ok": len(ok),
        "error_rate": round(1 - len(ok) / len(results), 4) if results else None,
        "errors": errors,
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "ttft_p50_s": percentile(ttfts, 50),
        "ttft_p95_s": percentile(ttfts, 95),
        "decode_tps_p50": percentile(per_request_tps, 50),
        "output_tokens_mean": statistics.fmean(r.output_tokens for r in ok) if ok else None,
        "throughput_tokens_per_s": sum(r.output_tokens for r in ok) / wall_s if wall_s else None,
        "requests_per_s": len(ok) / wall_s if wall_s else None,
        "wall_s": round(wall_s, 3),
    }


# --- Result files: append-only, fsync'd, resumable (GPU-05) ----------------------------------


@dataclass
class ResultLog:
    path: Path
    done: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a row cut off by a killed session: ignored, redone
                self.done.add(f"{row['cell_key']}|rep{row['repetition']}")

    def is_done(self, cell: Cell, repetition: int) -> bool:
        return f"{cell.key}|rep{repetition}" in self.done

    def append(self, row: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.done.add(f"{row['cell_key']}|rep{row['repetition']}")


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion from synchronous code, even inside a notebook.

    Jupyter (and papermill on Kaggle) already runs an event loop in the main thread, where
    `asyncio.run()` raises "cannot be called from a running event loop": the second Kaggle run
    failed there on 2026-10-07. In that case the coroutine gets its own loop on a worker thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def run_cell(
    cell: Cell,
    *,
    base_url: str,
    model: str,
    prompts: PromptSet,
    server: dict[str, Any],
    log: ResultLog,
    timeout_s: float = 300.0,
) -> list[dict[str, Any]]:
    """Run every repetition of a cell not already in the log. Returns the new rows."""
    rows = []
    for rep in range(1, cell.repetitions + 1):
        if log.is_done(cell, rep):
            continue
        run_sync(  # BEN-01: warm-up, discarded
            run_requests(
                base_url,
                model,
                prompts.prompts,
                cell.warmup,
                min(cell.concurrency, cell.warmup),
                cell.max_tokens,
                10_000 * rep,
                timeout_s,
            )
        )
        results, wall = run_sync(
            run_requests(
                base_url,
                model,
                prompts.prompts,
                cell.requests,
                cell.concurrency,
                cell.max_tokens,
                100_000 * rep,
                timeout_s,
            )
        )
        row = {
            "cell_key": cell.key,
            "repetition": rep,
            **asdict(cell),
            "prompt_set_sha256": prompts.sha256,
            "server": server,  # dtype, quantization, knobs, prefix caching (BEN-02)
            "gateway_cache": "not used: the harness calls the model server directly",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": platform.node(),
            **summarise(results, wall),
        }
        log.append(row)
        rows.append(row)
    return rows
