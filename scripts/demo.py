#!/usr/bin/env python3
"""End-to-end demo through the gateway (`make demo`).

Waits for /readyz, then asks a few questions that show the main behaviours: streaming progress
and a cited answer, a cache hit, a declined advice request and an off-topic question.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time

import httpx

QUESTIONS = [
    ("A cited answer, streamed", "What leverage limits apply to retail CFD clients in the EU?"),
    (
        "The same question again: served from the cache",
        "what leverage limits apply to retail CFD clients in the EU",
    ),
    ("Personal advice is declined by code, not left to the model", "Should I buy EUR/USD now?"),
    ("Off-topic questions are declined before any model call", "How do I bake sourdough bread?"),
]


def wait_ready(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last: dict | str = "no answer"
    while time.monotonic() < deadline:
        try:
            response = client.get("/readyz", timeout=10)
            last = response.json()
            if response.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(1)
    raise SystemExit(f"gateway not ready after {timeout:.0f}s: {last}")


def ask(client: httpx.Client, key: str, question: str) -> None:
    started = time.monotonic()
    stages: list[str] = []
    answer: dict | None = None
    with client.stream(
        "POST",
        "/v1/ask",
        json={"question": question},
        headers={"Authorization": f"Bearer {key}"},
    ) as response:
        if response.status_code != 200:
            response.read()
            print(f"  HTTP {response.status_code}: {response.text}")
            return
        name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if name == "status":
                    stages.append(data["stage"])
                    print(f"\r  progress: {' > '.join(stages)}", end="", flush=True)
                elif name == "answer":
                    answer = data
                elif name == "error":
                    print(f"\n  error: {data['code']} ({data['status']}): {data['message']}")
                    return
    seconds = time.monotonic() - started
    if stages:
        print()
    if answer is None:
        print("  the stream ended without an answer")
        return
    print(f"  outcome: {answer['outcome']}, cached: {answer['cached']}, {seconds:.2f}s")
    for line in textwrap.wrap(answer["answer"], 92):
        print(f"  | {line}")
    for c in answer["citations"]:
        page = f", p. {c['page']}" if c["page"] else ""
        print(f"  [{c['label']}] {c['publisher']}: {c['title']}{page}")
    print(f"  ({answer['disclaimer']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway", default="http://127.0.0.1:8000")
    parser.add_argument("--key", required=True)
    parser.add_argument("--ready-timeout", type=float, default=180)
    args = parser.parse_args()
    with httpx.Client(base_url=args.gateway, timeout=180) as client:
        print(f"Waiting for {args.gateway}/readyz ...")
        wait_ready(client, args.ready_timeout)
        headers = {"Authorization": f"Bearer {args.key}"}
        deadline = time.monotonic() + 45  # a new key is picked up at the next key refresh
        while (info_response := client.get("/v1/info", headers=headers)).status_code == 401:
            if time.monotonic() > deadline:
                raise SystemExit("the API key is still rejected after 45s")
            time.sleep(1)
        info = info_response.json()
        print(
            f"Gateway ready. Model: {info['model']}, cache: {'on' if info['cache_enabled'] else 'off'}"
        )
        for title, question in QUESTIONS:
            print(f"\n== {title}\n  Q: {question}")
            ask(client, args.key, question)
    return 0


if __name__ == "__main__":
    sys.exit(main())
