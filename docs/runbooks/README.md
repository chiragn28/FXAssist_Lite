# Runbooks

One runbook per failure drill: symptom, detection, root cause, fix, prevention. Each is written from a real drill, not from theory.

| Runbook | Phase | Status |
|---|---|---|
| [`qdrant-down.md`](qdrant-down.md) | 2 | Written from `make drill`; metrics and alert added in Phase 3 |
| [`redis-down.md`](redis-down.md) | 2 | Written from `make drill`; metrics and alert added in Phase 3 |
| `llm-timeout-storm.md` | 3 | PENDING |
| `pod-oomkilled.md` | 4 | PENDING |
| `watchdog-restart-loop.md` | 4 | PENDING |
| `gpu-oom.md` | 6 | PENDING |
| `kv-cache-exhaustion.md` | 6 | PENDING |
| `slow-model-load.md` | 6 | PENDING |
