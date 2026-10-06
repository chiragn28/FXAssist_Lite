"""One watchdog run (a Kubernetes CronJob runs it every minute; ADR-014).

    read state -> canary -> decide -> (restart) -> write state

Configuration (environment):
  FXA_WATCHDOG_LLM_URL        e.g. http://fxassist-mock-llm:8080/v1
  FXA_WATCHDOG_MODEL          model name to ask
  FXA_WATCHDOG_NAMESPACE      namespace of the deployment to restart
  FXA_WATCHDOG_DEPLOYMENT     the one deployment it may restart
  FXA_WATCHDOG_FAILURES       consecutive failures before a restart (3)
  FXA_WATCHDOG_COOLDOWN_S     no restart within this long after one (600)
  FXA_WATCHDOG_MAX_PER_HOUR   restart cap per hour (2)
  FXA_WATCHDOG_SLOW_S         slower canary answers count as failures (20)
  FXA_WATCHDOG_TIMEOUT_S      canary request timeout (30)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

from .canary import run_canary
from .k8s import KubeClient
from .policy import Policy, State, decide

log = logging.getLogger("fxassist.watchdog")


def run_once(kube: KubeClient, env: dict[str, str], now: float | None = None) -> str:
    policy = Policy(
        failures_to_restart=int(env.get("FXA_WATCHDOG_FAILURES", "3")),
        cooldown_s=float(env.get("FXA_WATCHDOG_COOLDOWN_S", "600")),
        max_restarts_per_hour=int(env.get("FXA_WATCHDOG_MAX_PER_HOUR", "2")),
        slow_threshold_s=float(env.get("FXA_WATCHDOG_SLOW_S", "20")),
    )
    state = State.from_dict(kube.read_state())
    canary = run_canary(
        env["FXA_WATCHDOG_LLM_URL"],
        env.get("FXA_WATCHDOG_MODEL", "mock-llm"),
        timeout_s=float(env.get("FXA_WATCHDOG_TIMEOUT_S", "30")),
        slow_threshold_s=policy.slow_threshold_s,
    )
    decision = decide(state, canary.healthy, time.time() if now is None else now, policy)
    record = {
        "canary": canary.reason,
        "action": decision.action,
        "reason": decision.reason,
        "consecutive_failures": decision.state.consecutive_failures,
        "restarts_last_hour": len(decision.state.restarts),
    }
    level = logging.INFO if decision.action == "none" else logging.WARNING
    log.log(level, json.dumps(record))  # one structured line per run, easy to grep
    if decision.action == "restart":
        kube.rollout_restart()
    kube.write_state(decision.state.to_dict())
    return decision.action


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one decision line per run, not three
    env = dict(os.environ)
    try:
        kube = KubeClient(env["FXA_WATCHDOG_NAMESPACE"], env["FXA_WATCHDOG_DEPLOYMENT"])
        run_once(kube, env)
    except KeyError as exc:
        log.error("missing configuration: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
