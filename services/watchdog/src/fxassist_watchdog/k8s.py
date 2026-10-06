"""The three Kubernetes API calls the watchdog needs, with the pod's service account.

Its Role allows only `get` and `patch` on one named Deployment (K8S-05), so this client cannot
touch anything else even if the code had a bug. State is kept in an annotation on that same
Deployment, because CronJob pods start fresh every run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
STATE_ANNOTATION = "fxassist.io/watchdog-state"


class KubeClient:
    def __init__(self, namespace: str, deployment: str, *, client: httpx.Client | None = None):
        self.path = f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"
        if client is None:
            token = (SA_DIR / "token").read_text().strip()
            client = httpx.Client(
                base_url="https://kubernetes.default.svc",
                verify=str(SA_DIR / "ca.crt"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        self.client = client

    def _patch(self, body: dict) -> None:
        response = self.client.patch(
            self.path,
            content=json.dumps(body),
            headers={"Content-Type": "application/merge-patch+json"},
        )
        response.raise_for_status()

    def read_state(self) -> dict | None:
        response = self.client.get(self.path)
        response.raise_for_status()
        raw = (response.json()["metadata"].get("annotations") or {}).get(STATE_ANNOTATION)
        return json.loads(raw) if raw else None

    def write_state(self, state: dict) -> None:
        self._patch({"metadata": {"annotations": {STATE_ANNOTATION: json.dumps(state)}}})

    def rollout_restart(self) -> None:
        """What `kubectl rollout restart` does: change a pod-template annotation."""
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._patch(
            {
                "spec": {
                    "template": {
                        "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}
                    }
                }
            }
        )
