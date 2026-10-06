# Runbook: pod OOMKilled

Written from two real events on kind (2026-10-07). K8S-02.

1. **Unplanned:** the first `make kind-deploy` failed because the ingestion Job was OOMKilled three times (`BackoffLimitExceeded`).
2. **Drill:** the mock LLM was made to allocate 200 MiB with a 128 Mi limit (`POST /_mock/hog?mb=200`).

## Symptom
- `kubectl get pods`: restart count goes up; status `OOMKilled` or `CrashLoopBackOff`; for a Job, `BackoffLimitExceeded` and Helm reports `Job ... not ready. status: Failed`.
- `kubectl get pod <pod> -o jsonpath='{.status.containerStatuses[0].lastState.terminated}'` shows `reason: OOMKilled`, `exitCode: 137` (128 + SIGKILL).
- The Job's pod may already be deleted, so `kubectl logs` shows nothing. **The kernel log still has it:** `docker exec fxassist-control-plane dmesg | grep -i "killed process"` showed `Memory cgroup out of memory: Killed process ... (fxassist) ... anon-rss:1563328kB`, three times: the container's limit was 1536 Mi.

## Detection
- In this stack: Kubernetes events (`kubectl get events --field-selector reason=BackOff`) and the restart count. kind runs no kube-state-metrics, so there is no Prometheus alert here.
- Where kube-state-metrics runs, the usual alert queries are:
  - `increase(kube_pod_container_status_restarts_total[10m]) > 0`
  - `max by (namespace, pod, container) (kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}) == 1`
  - Warning before it happens: `container_memory_working_set_bytes / on(...) kube_pod_container_resource_limits{resource="memory"} > 0.9`. (Documented, not run in this project.)

## Root cause (ingestion)
Embedding runs ONNX Runtime on batches of chunks. Attention memory grows with batch size x sequence length squared, so 64 long chunks per batch (the CLI default) peaked above 1.5 GiB. The first two attempts each got further before dying because ingestion is idempotent and resumable (DAT-02): chunks already stored were not embedded again. That is why a manual rerun "worked": it only had 375 of 727 chunks left.

## Fix
- Ingestion Job: `FXA_EMBED_BATCH_SIZE=16` (`ingest.embedBatchSize` in the chart). A full ingestion from an empty collection then peaked at about 931 MiB (sampled with `kubectl top` every 5 s) under the same 1536 Mi limit; the whole `helm upgrade --wait --wait-for-jobs`, including downloading all 26 documents and embedding 727 chunks, took 5 minutes.
- Mock LLM drill: nothing to fix; kubelet restarted the container and it was ready again within 8 s. Its in-memory config was reset by the restart (expected for the mock).

## Prevention
- Every container has memory requests and limits (`tests/test_helm.py`). Requests are what the scheduler reserves; the limit is where the kernel kills.
- Set limits from measurements, not guesses: gateway 349 MiB measured, limit 1 Gi; ingestion 931 MiB peak, limit 1.5 Gi.
- For batch work, bound memory by bounding batch size, not by raising limits until it fits.
