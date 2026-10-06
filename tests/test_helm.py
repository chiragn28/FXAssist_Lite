"""Static checks on the Helm chart, rendered with `helm template` (Phase 4).

K8S-01 probes, K8S-02 resources, K8S-05 RBAC scope, K8S-06 no secrets in values, K8S-07 rollout
safety, K8S-08 no image pulls on kind, K8S-09 small overrides, OBS-04, SAF-08. The behaviour on
a real cluster is checked by `make kind-rbac-check`, `make kind-rollout-test` and
`make kind-watchdog-drill`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "deploy" / "helm" / "fxassist"
VALUES_FILES = sorted(CHART.glob("values*.yaml"))

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def render(*extra: str) -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "fxassist", str(CHART), *extra],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


DOCS = render() if shutil.which("helm") else []
KIND_DOCS = render("-f", str(CHART / "values-kind.yaml")) if shutil.which("helm") else []


def pod_specs(docs: list[dict]) -> list[tuple[str, dict]]:
    specs = []
    for d in docs:
        spec = d.get("spec", {})
        if d["kind"] in ("Deployment", "StatefulSet", "Job"):
            specs.append((d["metadata"]["name"], spec["template"]["spec"]))
        elif d["kind"] == "CronJob":
            specs.append((d["metadata"]["name"], spec["jobTemplate"]["spec"]["template"]["spec"]))
    return specs


def containers(docs: list[dict]) -> list[tuple[str, dict]]:
    return [
        (f"{name}/{c['name']}", c)
        for name, spec in pod_specs(docs)
        for c in spec.get("containers", []) + spec.get("initContainers", [])
    ]


def by_kind_name(docs: list[dict], kind: str, name: str) -> dict:
    return next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name)


def test_chart_renders_every_component() -> None:
    names = {(d["kind"], d["metadata"]["name"]) for d in DOCS}
    for expected in [
        ("Deployment", "fxassist-gateway"),
        ("Deployment", "fxassist-mock-llm"),
        ("StatefulSet", "fxassist-qdrant"),
        ("Deployment", "fxassist-redis"),
        ("StatefulSet", "fxassist-postgres"),
        ("Job", "fxassist-ingest-1"),
        ("CronJob", "fxassist-watchdog"),
        ("HorizontalPodAutoscaler", "fxassist-gateway"),
        ("PodDisruptionBudget", "fxassist-gateway"),
    ]:
        assert expected in names, expected


@pytest.mark.parametrize("name", [n for n, _ in containers(DOCS)])
def test_k8s02_every_container_has_requests_and_limits(name: str) -> None:
    resources = dict(containers(DOCS))[name].get("resources") or {}
    if name.endswith("/wait-for-qdrant"):  # init container: runs before the main one
        return
    for kind in ("requests", "limits"):
        assert {"cpu", "memory"} <= set(resources.get(kind, {})), f"{name}: {kind}"


@pytest.mark.parametrize("name", [n for n, _ in containers(DOCS)])
def test_saf08_every_container_is_non_root_without_capabilities(name: str) -> None:
    sc = dict(containers(DOCS))[name]["securityContext"]
    assert sc["runAsNonRoot"] is True and sc["runAsUser"] != 0
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"]["drop"] == ["ALL"]
    assert sc["seccompProfile"]["type"] == "RuntimeDefault"


def test_k8s01_gateway_probes_protect_slow_starts() -> None:
    gateway = by_kind_name(DOCS, "Deployment", "fxassist-gateway")
    c = gateway["spec"]["template"]["spec"]["containers"][0]
    startup = c["startupProbe"]
    assert startup["httpGet"]["path"] == "/healthz"
    assert startup["periodSeconds"] * startup["failureThreshold"] >= 120  # seconds to start
    assert c["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert c["livenessProbe"]["httpGet"]["path"] == "/healthz"  # never dependencies


def test_k8s07_rollouts_keep_capacity_and_drain() -> None:
    gateway = by_kind_name(DOCS, "Deployment", "fxassist-gateway")
    assert gateway["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    pod = gateway["spec"]["template"]["spec"]
    pre_stop = int(pod["containers"][0]["lifecycle"]["preStop"]["exec"]["command"][1])
    config = by_kind_name(DOCS, "ConfigMap", "fxassist-gateway")["data"]
    app_grace = float(config["FXA_SHUTDOWN_GRACE_S"])
    assert pod["terminationGracePeriodSeconds"] > pre_stop + app_grace
    pdb = by_kind_name(DOCS, "PodDisruptionBudget", "fxassist-gateway")
    assert pdb["spec"]["minAvailable"] >= 1
    assert (
        by_kind_name(DOCS, "HorizontalPodAutoscaler", "fxassist-gateway")["spec"]["minReplicas"]
        >= 2
    )


def test_k8s05_watchdog_role_allows_one_deployment_only() -> None:
    role = by_kind_name(DOCS, "Role", "fxassist-watchdog")
    assert role["rules"] == [
        {
            "apiGroups": ["apps"],
            "resources": ["deployments"],
            "resourceNames": ["fxassist-mock-llm"],
            "verbs": ["get", "patch"],
        }
    ]
    assert not [d for d in DOCS if d["kind"] in ("ClusterRole", "ClusterRoleBinding")]
    others = [n for n, spec in pod_specs(DOCS) if spec.get("serviceAccountName")]
    assert others == ["fxassist-watchdog"]  # nothing else gets API credentials
    no_token = [
        n for n, spec in pod_specs(DOCS) if spec.get("automountServiceAccountToken") is False
    ]
    assert len(no_token) == len(pod_specs(DOCS)) - 1


SECRET_KEY = re.compile(r"(password|secret|token|api[_-]?key)", re.I)


@pytest.mark.parametrize("path", VALUES_FILES, ids=[p.name for p in VALUES_FILES])
def test_k8s06_values_files_hold_no_secrets(path: Path) -> None:
    def walk(node, where=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{where}.{k}")
        else:
            yield where, node

    for where, _value in walk(yaml.safe_load(path.read_text()) or {}):
        if SECRET_KEY.search(where):
            assert where.endswith("secretName"), f"{path.name}{where}: secret-like value in values"


def test_k8s06_chart_creates_no_secret_and_reads_one_by_reference() -> None:
    assert not [d for d in DOCS if d["kind"] == "Secret"]
    text = (CHART / "templates" / "gateway.yaml").read_text()
    assert "secretKeyRef" in text and "postgres-password" in text


def test_k8s08_kind_never_pulls_app_images() -> None:
    for name, c in containers(KIND_DOCS):
        if c["image"].startswith("fxassist/"):
            assert c["imagePullPolicy"] == "Never", name
    assert "kind load image-archive" in (ROOT / "Makefile").read_text()


def test_k8s09_environment_overrides_stay_small() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("helm_diff", ROOT / "scripts" / "helm_diff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in VALUES_FILES:
        if path.name == "values.yaml":
            continue
        keys = module.leaves(yaml.safe_load(path.read_text()) or {})
        assert len(keys) <= module.MAX_OVERRIDE_KEYS, (path.name, keys)


def test_obs04_metrics_service_is_cluster_internal() -> None:
    for docs in (DOCS, KIND_DOCS):
        metrics = by_kind_name(docs, "Service", "fxassist-gateway-metrics")
        assert metrics["spec"]["type"] == "ClusterIP"
