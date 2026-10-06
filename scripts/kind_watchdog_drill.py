#!/usr/bin/env python3
"""K8S-03 drill on kind: break the mock LLM and check the watchdog restarts it exactly once.

  1. clear the watchdog's state; the mock is healthy
  2. break the mock (it answers HTTP 500): expect warn, warn, restart (one per minute)
  3. the restart brings up a fresh, healthy pod; break that one too, straight away
  4. expect warnings and "suppressed" (cooldown), but no second restart
  5. heal it and report

Takes about 8 minutes because the CronJob runs once a minute. Needs `make kind-deploy`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

DEPLOYMENT = "fxassist-mock-llm"
STATE = "fxassist.io/watchdog-state"


def kubectl(ns: str, *args: str, check: bool = True) -> str:
    out = subprocess.run(["kubectl", "-n", ns, *args], capture_output=True, text=True, check=False)  # noqa: S603,S607
    if check and out.returncode != 0:
        raise SystemExit(f"kubectl {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def mock_config(ns: str, body: dict) -> None:
    code = (
        "import json,urllib.request as u;"
        f"r=u.Request('http://127.0.0.1:8080/_mock/config',data=json.dumps({body!r}).encode(),"
        "headers={'Content-Type':'application/json'});u.urlopen(r,timeout=5)"
    )
    kubectl(ns, "exec", f"deploy/{DEPLOYMENT}", "--", "python", "-c", code)


def deployment(ns: str) -> dict:
    return json.loads(kubectl(ns, "get", "deployment", DEPLOYMENT, "-o", "json"))


def restarted_at(dep: dict) -> str | None:
    return (dep["spec"]["template"]["metadata"].get("annotations") or {}).get(
        "kubectl.kubernetes.io/restartedAt"
    )


def state(dep: dict) -> dict:
    raw = (dep["metadata"].get("annotations") or {}).get(STATE)
    return json.loads(raw) if raw else {}


def last_watchdog_log(ns: str) -> str:
    jobs = json.loads(
        kubectl(ns, "get", "jobs", "-l", "app.kubernetes.io/component=watchdog", "-o", "json")
    )
    items = sorted(jobs["items"], key=lambda j: j["metadata"]["creationTimestamp"])
    if not items:
        return "(no run yet)"
    logs = kubectl(ns, "logs", f"job/{items[-1]['metadata']['name']}", check=False)
    decisions = [line for line in logs.splitlines() if '"action"' in line]  # the JSON line
    return decisions[-1] if decisions else "(no decision logged yet)"


def wait_rollout(ns: str) -> None:
    kubectl(ns, "rollout", "status", f"deployment/{DEPLOYMENT}", "--timeout=180s")


def watch(ns: str, minutes: int, label: str, seen: list[str]) -> None:
    last = None
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        line = last_watchdog_log(ns)
        if line != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {label}: {line}")
            last = line
            seen.append(line)
        time.sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="fxassist")
    args = parser.parse_args()
    ns = args.namespace

    print("1. resetting watchdog state; mock healthy")
    kubectl(ns, "annotate", "deployment", DEPLOYMENT, f"{STATE}-", "--overwrite", check=False)
    wait_rollout(ns)
    mock_config(ns, {})
    before = restarted_at(deployment(ns))
    print(f"   restartedAt before: {before}")

    print("2. breaking the mock (HTTP 500 on every call)")
    mock_config(ns, {"error_status": 500})
    seen: list[str] = []
    deadline = time.monotonic() + 6 * 60
    while restarted_at(deployment(ns)) == before:
        if time.monotonic() > deadline:
            print("FAILED: no restart within 6 minutes")
            return 1
        watch(ns, 0.5, "broken", seen)
    first_restart = restarted_at(deployment(ns))
    print(f"   restarted at {first_restart}; waiting for the new pod")
    wait_rollout(ns)

    print("3. breaking the new pod straight away: the cooldown must prevent a second restart")
    mock_config(ns, {"error_status": 500})
    watch(ns, 4.5, "broken again", seen)
    after = restarted_at(deployment(ns))
    restarts = state(deployment(ns)).get("restarts", [])

    print("4. healing the mock")
    mock_config(ns, {})
    ok = after == first_restart and len(restarts) == 1 and any('"suppressed"' in s for s in seen)
    print(
        f"\nrestarts recorded: {len(restarts)}; second restart prevented: {after == first_restart}"
    )
    print("watchdog drill " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
