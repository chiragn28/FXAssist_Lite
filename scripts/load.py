#!/usr/bin/env python3
"""A small mixed load for filling the dashboard (`make load`). Not a benchmark.

Sends questions from eval/questions.yaml with a few concurrent workers for a fixed time: mostly
answerable ones (with repeats, so the cache gets hits), some off-topic, vague and advice
questions. Benchmarks need the cache off and a fixed method (ADR-017); this script is for
seeing the system move, so it refuses to call its output a benchmark.
"""

from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent


def question_pool(seed: int) -> list[str]:
    items = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text())
    items = items.get("questions", items) if isinstance(items, dict) else items
    by_kind: dict[str, list[str]] = {}
    for item in items:
        by_kind.setdefault(item.get("category", "other"), []).append(item["question"])
    rng = random.Random(seed)  # noqa: S311 - load shaping, not security
    pool: list[str] = []
    for kind, questions in by_kind.items():
        weight = 4 if kind == "answerable" else 1
        pool += questions * weight
    rng.shuffle(pool)
    return pool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway", default="http://127.0.0.1:8000")
    parser.add_argument("--key", action="append", required=True, help="repeat: one per worker")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    pool = question_pool(args.seed)
    keys = args.key
    with httpx.Client(base_url=args.gateway, timeout=30) as client:
        deadline = time.monotonic() + 45  # a new key is active after the next key refresh
        for key in keys:
            headers = {"Authorization": f"Bearer {key}"}
            while client.get("/v1/info", headers=headers).status_code == 401:
                if time.monotonic() > deadline:
                    print("error: API key still rejected after 45s", file=sys.stderr)
                    return 1
                time.sleep(1)

    results: Counter[str] = Counter()
    lock = threading.Lock()
    stop_at = time.monotonic() + args.seconds

    def worker(n: int) -> None:
        rng = random.Random(args.seed + n)  # noqa: S311
        headers = {"Authorization": f"Bearer {keys[n % len(keys)]}"}
        with httpx.Client(base_url=args.gateway, timeout=120) as client:
            while time.monotonic() < stop_at:
                question = rng.choice(pool)
                try:
                    r = client.post(
                        "/v1/ask", json={"question": question, "stream": False}, headers=headers
                    )
                    label = (
                        r.json().get("outcome") if r.status_code == 200 else f"HTTP {r.status_code}"
                    )
                except httpx.HTTPError as exc:
                    label = type(exc).__name__
                with lock:
                    results[label] += 1
                if label == "HTTP 429":  # stay under the per-key limit (API-02)
                    time.sleep(float(r.headers.get("retry-after", "1")))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(args.workers)]
    print(f"Sending load for {args.seconds:.0f}s with {args.workers} workers (not a benchmark)...")
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = sum(results.values())
    print(f"{total} requests: " + ", ".join(f"{k} {v}" for k, v in results.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
