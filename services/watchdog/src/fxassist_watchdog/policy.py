"""The restart decision, as a pure function of state, canary result and time (K8S-03, K8S-04).

Hysteresis, so a flaky or slow-but-healthy model is not restarted in a loop:
  - one failure is a warning, not an action; only `failures_to_restart` consecutive failures
    trigger a restart (K8S-04: warning before action)
  - a slow answer under `slow_threshold_s` is healthy; slower counts as a failure, because a
    model that takes longer than users will wait is broken for them
  - after a restart, no other restart for `cooldown_s` (the new pod needs time to load)
  - at most `max_restarts_per_hour` restarts in any hour; past that, keep warning and leave it
    to a human, because restarting is clearly not fixing it (K8S-03)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Action = Literal["none", "warn", "restart", "suppressed"]


@dataclass(frozen=True)
class Policy:
    failures_to_restart: int = 3
    cooldown_s: float = 600.0
    max_restarts_per_hour: int = 2
    slow_threshold_s: float = 20.0


@dataclass
class State:
    consecutive_failures: int = 0
    restarts: list[float] = field(default_factory=list)  # timestamps of restarts in the last hour

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> State:
        data = data or {}
        return cls(
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            restarts=[float(t) for t in data.get("restarts", [])],
        )


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    state: State


def decide(state: State, healthy: bool, now: float, policy: Policy) -> Decision:
    restarts = [t for t in state.restarts if now - t < 3600]
    if healthy:
        return Decision("none", "canary passed", State(0, restarts))
    failures = state.consecutive_failures + 1
    if failures < policy.failures_to_restart:
        reason = f"canary failed ({failures}/{policy.failures_to_restart}); no action yet"
        return Decision("warn", reason, State(failures, restarts))
    if restarts and now - restarts[-1] < policy.cooldown_s:
        wait = policy.cooldown_s - (now - restarts[-1])
        return Decision(
            "suppressed",
            f"in cooldown after the last restart ({wait:.0f}s left)",
            State(failures, restarts),
        )
    if len(restarts) >= policy.max_restarts_per_hour:
        return Decision(
            "suppressed",
            f"{len(restarts)} restarts in the last hour (limit {policy.max_restarts_per_hour}); "
            "a human needs to look",
            State(failures, restarts),
        )
    return Decision(
        "restart", f"{failures} consecutive canary failures", State(0, [*restarts, now])
    )
