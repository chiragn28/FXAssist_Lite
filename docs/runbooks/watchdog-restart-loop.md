# Runbook: watchdog restart loop

Written from `make kind-watchdog-drill` on kind (2026-10-07). K8S-03, K8S-04, ADR-014.

## What the watchdog does
Every minute a CronJob sends a canary prompt ("Reply with the single word OK.") to the model server. A failure is an error status, an empty or malformed answer, no answer within 30 s, or an answer slower than 20 s. After **3 consecutive** failures it restarts the model Deployment (`kubectl rollout restart` equivalent), then:
- no other restart for **10 minutes** (cooldown: the new pod needs time to load the model),
- at most **2 restarts per hour**; after that it only warns: restarting is clearly not fixing it, and a human must look.
Its state (consecutive failures, restart times) is an annotation on the Deployment it watches: `kubectl -n fxassist get deploy fxassist-mock-llm -o jsonpath='{.metadata.annotations.fxassist\.io/watchdog-state}'`.

## Symptom of a loop (what the policy prevents)
The model pod is restarted again and again: restart timestamps every few minutes, the model never finishes loading, each restart throws away the work of the last one. Without hysteresis this happens whenever the root cause is not fixed by a restart (bad config, out of GPU memory, a broken model file) or the canary is too strict for a slow but healthy model.

## Detection
One JSON line per run in the CronJob's Job logs:
```bash
kubectl -n fxassist logs job/$(kubectl -n fxassist get jobs -l app.kubernetes.io/component=watchdog \
    --sort-by=.metadata.creationTimestamp -o name | tail -1 | cut -d/ -f2)
```
`"action": "warn"` (failure counted, no action), `"restart"`, `"suppressed"` (cooldown or hourly cap reached: page a human).

## The drill (what happened)
| Time (UTC) | Watchdog decision |
|---|---|
| 21:27 | mock broken (HTTP 500): `warn`, 1/3 |
| 21:28 | `warn`, 2/3 |
| 21:29 | `restart`: "3 consecutive canary failures" |
| 21:29 | new pod up; broken again straight away |
| 21:30, 21:31 | `warn` 1/3, 2/3 |
| 21:32 | `suppressed`: "in cooldown after the last restart (420s left)" |
| 21:33 | `suppressed` (360 s left) |

Exactly one restart. Unit tests cover the hourly cap over a full broken hour (2 restarts, then only warnings) and a slow-but-under-threshold model (no failure).

## Fix
- `suppressed` with the hourly cap reached: restarting does not help. Look at the model server's logs and events (`kubectl describe pod`, OOMKilled? image pull? GPU memory?). See `pod-oomkilled.md`, and from Phase 6 `gpu-oom.md` and `slow-model-load.md`.
- False positives on a slow but healthy model: raise `watchdog.slowThresholdSeconds` / `timeoutSeconds` in the chart before raising failure counts.
- Reset the state after fixing: `kubectl -n fxassist annotate deploy fxassist-mock-llm fxassist.io/watchdog-state-`.

## Permissions (K8S-05)
The watchdog's Role allows `get` and `patch` on `deployments/fxassist-mock-llm` and nothing else; `make kind-rbac-check` proves 17 allow/deny cases with `kubectl auth can-i`.
