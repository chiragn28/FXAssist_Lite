"""Watchdog: restart policy (K8S-03, K8S-04), canary checks and the Kubernetes calls."""

from __future__ import annotations

import json

import httpx
import pytest

from fxassist_mock_llm.app import Behaviour, create_app
from fxassist_mock_llm.server import serve
from fxassist_watchdog.canary import run_canary
from fxassist_watchdog.k8s import STATE_ANNOTATION, KubeClient
from fxassist_watchdog.main import run_once
from fxassist_watchdog.policy import Policy, State, decide

POLICY = Policy(failures_to_restart=3, cooldown_s=600, max_restarts_per_hour=2, slow_threshold_s=5)


def run(results: list[bool], start: float = 0.0, step: float = 60.0) -> list[str]:
    """Feed a sequence of canary results, one per minute; return the actions."""
    state, actions = State(), []
    for i, healthy in enumerate(results):
        decision = decide(state, healthy, start + i * step, POLICY)
        state = decision.state
        actions.append(decision.action)
    return actions


# --- K8S-04: no action on a single blip; warnings first ------------------------------------


def test_k8s04_single_or_intermittent_failures_only_warn() -> None:
    assert run([False, True, False, True, False, False, True]) == [
        "warn",
        "none",
        "warn",
        "none",
        "warn",
        "warn",
        "none",
    ]


def test_k8s04_restart_needs_n_consecutive_failures_after_warnings() -> None:
    assert run([False, False, False]) == ["warn", "warn", "restart"]


# --- K8S-03: no restart loops ------------------------------------------------------------------


def test_k8s03_cooldown_blocks_a_second_restart() -> None:
    # Still broken after the restart: 3 more failures inside the 10-minute cooldown.
    assert run([False] * 6) == ["warn", "warn", "restart", "warn", "warn", "suppressed"]


def test_k8s03_hourly_cap_then_hand_over_to_a_human() -> None:
    actions = run([False] * 60)  # broken for an hour, checked every minute
    assert actions.count("restart") == 2  # max_restarts_per_hour
    last_restart = len(actions) - 1 - actions[::-1].index("restart")
    assert set(actions[last_restart + 1 :]) <= {"warn", "suppressed"}


def test_k8s03_cap_window_slides() -> None:
    state = State(consecutive_failures=2, restarts=[0.0, 700.0])
    assert decide(state, False, 1500, POLICY).action == "suppressed"  # 2 in the last hour
    assert decide(state, False, 3700, POLICY).action == "restart"  # the first aged out


def test_recovery_resets_the_failure_count() -> None:
    state = State(consecutive_failures=2, restarts=[])
    decision = decide(state, True, 100, POLICY)
    assert decision.action == "none" and decision.state.consecutive_failures == 0


def test_state_round_trips_through_json() -> None:
    state = State(consecutive_failures=2, restarts=[1.5, 2.5])
    assert State.from_dict(json.loads(json.dumps(state.to_dict()))) == state
    assert State.from_dict(None) == State()


# --- Canary ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_url():
    with serve(create_app(Behaviour(models=["mock-llm"]))) as server:
        yield server.url


@pytest.fixture
def mock(mock_url):
    with httpx.Client(base_url=mock_url) as client:
        client.delete("/_mock/config")
        yield client


@pytest.mark.parametrize(
    "config,healthy,reason",
    [
        ({}, True, "answered"),
        ({"error_status": 503}, False, "HTTP 503"),
        ({"empty": True}, False, "empty answer"),
        ({"latency_ms": 700}, False, "too slow"),  # K8S-04: slow beyond the threshold
        ({"latency_ms": 200}, True, "answered"),  # slow but under it: healthy
        ({"hang": True}, False, "no answer within"),
    ],
)
def test_canary_judges_status_content_and_latency(mock_url, mock, config, healthy, reason) -> None:
    mock.post("/_mock/config", json=config)
    result = run_canary(f"{mock_url}/v1", "mock-llm", timeout_s=1, slow_threshold_s=0.5)
    assert result.healthy is healthy and reason in result.reason


def test_canary_reports_an_unreachable_server() -> None:
    result = run_canary("http://127.0.0.1:9/v1", "m", timeout_s=1, slow_threshold_s=1)
    assert not result.healthy and "cannot reach" in result.reason


# --- Kubernetes calls ----------------------------------------------------------------------


class FakeApi:
    """Records requests; holds one Deployment's annotations."""

    def __init__(self) -> None:
        self.annotations: dict[str, str] = {}
        self.patches: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/apis/apps/v1/namespaces/fx/deployments/fxassist-mock-llm"
        if request.method == "GET":
            return httpx.Response(200, json={"metadata": {"annotations": self.annotations}})
        assert request.method == "PATCH"
        assert request.headers["content-type"] == "application/merge-patch+json"
        body = json.loads(request.content)
        self.patches.append(body)
        self.annotations.update(body.get("metadata", {}).get("annotations", {}))
        return httpx.Response(200, json={})


@pytest.fixture
def api():
    fake = FakeApi()
    client = httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(fake.handler))
    return fake, KubeClient("fx", "fxassist-mock-llm", client=client)


def test_k8s03_broken_model_is_restarted_exactly_once_per_policy(mock_url, mock, api) -> None:
    fake, kube = api
    mock.post("/_mock/config", json={"error_status": 500})
    env = {
        "FXA_WATCHDOG_LLM_URL": f"{mock_url}/v1",
        "FXA_WATCHDOG_TIMEOUT_S": "2",
        "FXA_WATCHDOG_COOLDOWN_S": "600",
    }
    actions = [run_once(kube, env, now=1000 + 60 * i) for i in range(8)]
    assert actions == [
        "warn",
        "warn",
        "restart",
        "warn",
        "warn",
        "suppressed",
        "suppressed",
        "suppressed",
    ]
    restarts = [p for p in fake.patches if "spec" in p]
    assert len(restarts) == 1
    assert (
        "kubectl.kubernetes.io/restartedAt"
        in restarts[0]["spec"]["template"]["metadata"]["annotations"]
    )
    state = json.loads(fake.annotations[STATE_ANNOTATION])
    assert state["restarts"] == [1120.0]


def test_healthy_model_is_left_alone(mock_url, mock, api) -> None:
    fake, kube = api
    env = {"FXA_WATCHDOG_LLM_URL": f"{mock_url}/v1"}
    assert [run_once(kube, env, now=float(i)) for i in range(3)] == ["none"] * 3
    assert not [p for p in fake.patches if "spec" in p]
