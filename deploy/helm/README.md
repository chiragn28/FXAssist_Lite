# Helm chart: fxassist

The whole stack on Kubernetes, built for kind (ADR-013): no GPU, the mock LLM stands in for vLLM (ADR-003). Helm 4, chart `apiVersion: v2`.

```
                 NodePort 30080 (kind) / ClusterIP
client ──────────────► gateway (2-4 pods, HPA) ──► qdrant (StatefulSet, PVC)
                          │   │   └──────────────► redis
                          │   └──────────────────► postgres (StatefulSet, PVC)
                          └──────────────────────► mock-llm ◄── watchdog CronJob (get/patch this one Deployment)
ingest Job (per release revision): downloads the documents, embeds them into qdrant
```

| Values file | Purpose |
|---|---|
| `values.yaml` | Everything, with comments: resources (K8S-02), probes (K8S-01), rollout and drain timings (K8S-07), watchdog policy (K8S-03/04) |
| `values-kind.yaml` | 3 overrides: NodePort, `pullPolicy: Never` (K8S-08) |
| `values-ci.yaml` | 9 overrides: kind settings plus 1 gateway replica, no HPA, no PDB minimum |

`make helm-check` prints how each environment's rendered output differs from the base (K8S-09). `tests/test_helm.py` checks the rendered manifests: limits on every container, non-root everywhere, probe budgets, rollout settings, the watchdog's RBAC and that values hold no secrets.

## Secrets (K8S-06)

The chart creates no Secret. It reads `postgres-password` from the Secret named by `secretName` (`fxassist-secrets`), which `scripts/kind-secrets.sh` creates from your local `.env`.

## Design notes

- **Ingestion is a plain Job, not a Helm hook.** The gateway is not ready until the collection exists, so a post-install hook would wait on the gateway while `helm --wait` waits on the hook. A new Job per revision (Job specs are immutable); re-ingesting is idempotent (DAT-02). Embedding batch 16: batch 64 was OOMKilled at 1.5 GiB.
- **Probes:** startup `/healthz` (180 s budget), readiness `/readyz` (dependencies), liveness `/healthz` (process only).
- **Rollouts:** `maxUnavailable: 0`, `maxSurge: 1`, `preStop: sleep 5`, `terminationGracePeriodSeconds: 45` (> 5 s + the app's 30 s drain), PDB `minAvailable: 1`.
- **Security:** non-root, no privilege escalation, all capabilities dropped, RuntimeDefault seccomp, read-only root filesystem where the image allows it, API tokens mounted only into the watchdog.
- **Observability:** metrics on port 9464 behind a separate ClusterIP Service (OBS-04). No Prometheus on kind; the compose stack has it.
