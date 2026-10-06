#!/usr/bin/env python3
"""Dependency drills against the running stack (Phase 2 acceptance; CAC-01, DEP-01, DEP-02).

Stops Redis, PostgreSQL and Qdrant one at a time with `docker compose stop`, checks that the
gateway behaves as EDGE_CASES.md says, starts the service again and waits until the gateway is
ready before the next drill. Run with `make drill` (needs `make up` and an API key).

Usage: drill.py --gateway http://127.0.0.1:8000 --key fxa_... [--compose "docker compose ..."]
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
import uuid

import httpx

QUESTION = "What is a margin call?"


def compose(cmd: str, *args: str) -> None:
    subprocess.run([*shlex.split(cmd), *args], check=True, capture_output=True)  # noqa: S603


class Drill:
    def __init__(self, gateway: str, key: str, compose_cmd: str):
        self.client = httpx.Client(base_url=gateway, timeout=120)
        self.headers = {"Authorization": f"Bearer {key}"}
        self.compose_cmd = compose_cmd
        self.failures: list[str] = []

    def ask(self) -> httpx.Response:
        # A unique suffix keeps the cache out of the way: each drill measures a real run.
        question = f"{QUESTION} ({uuid.uuid4().hex[:6]})"
        return self.client.post(
            "/v1/ask", json={"question": question, "stream": False}, headers=self.headers
        )

    def ready(self) -> tuple[int, dict]:
        try:
            response = self.client.get("/readyz", timeout=10)
            return response.status_code, response.json()
        except httpx.HTTPError as exc:
            return 0, {"status": f"unreachable ({type(exc).__name__})"}

    def wait_for_key(self, timeout: float = 45) -> None:
        """New keys are picked up at the gateway's next key refresh (FXA_KEY_REFRESH_S)."""
        deadline = time.monotonic() + timeout
        while self.client.get("/v1/info", headers=self.headers).status_code == 401:
            if time.monotonic() > deadline:
                raise SystemExit(f"API key still rejected after {timeout:.0f}s")
            time.sleep(1)

    def wait_ready(self, timeout: float = 90) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code, body = self.ready()
            if code == 200 and body.get("status") == "ready":
                return
            time.sleep(1)
        raise SystemExit(f"gateway not ready after {timeout}s: {self.ready()}")

    def check(self, what: str, ok: bool, detail: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {what}: {detail}")
        if not ok:
            self.failures.append(what)

    def run(self, service: str, expect_status: int, expect_ready: str, row: str) -> None:
        print(f"\n{row}: stopping {service}")
        compose(self.compose_cmd, "stop", service)
        try:
            time.sleep(1)  # let connections notice
            response = self.ask()
            code = response.status_code
            detail = f"HTTP {code}"
            if code != 200:
                detail += f" {response.json().get('error', {}).get('code')}"
            self.check(f"{row} request", code == expect_status, detail)
            ready_code, body = self.ready()
            self.check(
                f"{row} /readyz",
                body.get("status") == expect_ready,
                f"HTTP {ready_code} {body.get('status')}",
            )
        finally:
            print(f"  starting {service} again")
            compose(self.compose_cmd, "start", service)
        started = time.monotonic()
        self.wait_ready()
        self.check(f"{row} recovery", True, f"ready again in {time.monotonic() - started:.1f}s")
        code = self.ask().status_code
        self.check(f"{row} after recovery", code == 200, f"HTTP {code}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gateway", default=os.environ.get("FXA_GATEWAY_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument("--key", default=os.environ.get("FXA_API_KEY"))
    parser.add_argument("--compose", default=os.environ.get("FXA_COMPOSE", "docker compose"))
    args = parser.parse_args()
    if not args.key:
        print(
            "error: pass --key or set FXA_API_KEY (create one with: make api-key)", file=sys.stderr
        )
        return 2
    drill = Drill(args.gateway, args.key, args.compose)
    drill.wait_ready(30)
    drill.wait_for_key()
    drill.run("redis", 200, "degraded", "CAC-01 Redis down")
    drill.run("postgres", 200, "degraded", "DEP-02 PostgreSQL down")
    drill.run("qdrant", 503, "not_ready", "DEP-01 Qdrant down")
    print(
        f"\n{'all drills passed' if not drill.failures else 'FAILED: ' + ', '.join(drill.failures)}"
    )
    return 1 if drill.failures else 0


if __name__ == "__main__":
    sys.exit(main())
