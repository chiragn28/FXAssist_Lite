#!/usr/bin/env python3
"""K8S-07: roll the gateway Deployment while traffic flows; expect zero failed requests.

Readiness gates, maxUnavailable 0, the preStop sleep and the app's own draining (API-08) must
together keep every request answered while pods are replaced. Needs `make kind-deploy`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from collections import Counter

import httpx


def kubectl(ns: str, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["kubectl", "-n", ns, *args],
        capture_output=True,
        text=True,
        check=True,  # noqa: S607
    ).stdout


def create_key(ns: str) -> str:
    out = kubectl(
        ns,
        "exec",
        "deploy/fxassist-gateway",
        "--",
        "fxassist-gateway",
        "create-key",
        "--name",
        "rollout-test",
    )
    return out.strip().splitlines()[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="fxassist")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    ns = args.namespace

    keys = [create_key(ns) for _ in range(args.workers)]
    # Every replica refreshes its key snapshot on its own timer (ADR-025, 30 s), so one 200
    # proves only that one pod knows the key. Require a run of successes over fresh
    # connections, which kube-proxy spreads across pods.
    print("waiting until every gateway pod accepts the new keys (up to 60 s)...")
    for key in keys:
        deadline, streak = time.monotonic() + 60, 0
        while streak < 8:
            if time.monotonic() > deadline:
                print("key not accepted by every pod")
                return 1
            with httpx.Client(base_url=args.gateway, timeout=10) as c:  # new connection
                ok = (
                    c.get("/v1/info", headers={"Authorization": f"Bearer {key}"}).status_code == 200
                )
            streak = streak + 1 if ok else 0
            time.sleep(0.25 if ok else 1)

    results: Counter[str] = Counter()
    failures: list[str] = []
    stop = threading.Event()

    def worker(key: str, n: int) -> None:
        with httpx.Client(base_url=args.gateway, timeout=60) as c:
            i = 0
            while not stop.is_set():
                i += 1
                try:
                    r = c.post(
                        "/v1/ask",
                        json={"question": f"What is a margin call? ({n}-{i})", "stream": False},
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    label = str(r.status_code)
                    if r.status_code != 200:
                        failures.append(f"HTTP {r.status_code}: {r.text[:120]}")
                except httpx.HTTPError as exc:
                    label = type(exc).__name__
                    failures.append(label)
                results[label] += 1
                time.sleep(2.2)  # stays under 30 requests a minute per key (API-02)

    threads = [threading.Thread(target=worker, args=(k, n)) for n, k in enumerate(keys)]
    for t in threads:
        t.start()
    time.sleep(5)
    pods_before = kubectl(
        ns, "get", "pods", "-l", "app.kubernetes.io/component=gateway", "-o", "name"
    ).split()
    print(f"rolling {len(pods_before)} gateway pod(s) while traffic flows...")
    started = time.monotonic()
    kubectl(ns, "rollout", "restart", "deployment/fxassist-gateway")
    kubectl(ns, "rollout", "status", "deployment/fxassist-gateway", "--timeout=300s")
    rollout_s = time.monotonic() - started
    for pod in pods_before:  # old pods drain for up to terminationGracePeriodSeconds
        kubectl(ns, "wait", "--for=delete", pod, "--timeout=120s")
    time.sleep(10)  # traffic keeps flowing on the new pods
    stop.set()
    for t in threads:
        t.join()
    pods_after = kubectl(
        ns, "get", "pods", "-l", "app.kubernetes.io/component=gateway", "-o", "name"
    ).split()
    replaced = not set(pods_before) & set(pods_after)
    total = sum(results.values())
    print(f"rollout took {rollout_s:.0f}s; all pods replaced: {replaced}")
    print(
        f"{total} requests during the test: "
        + ", ".join(f"{k} x{v}" for k, v in results.most_common())
    )
    for f in failures[:10]:
        print(f"  failure: {f}")
    ok = not failures and replaced and total > 0
    print("rollout test " + ("PASSED: zero failed requests" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
