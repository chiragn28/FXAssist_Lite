# Runbook: disk full on a data volume

Written from a drill on 2026-10-07: `postgres:18.6` with its data directory on a 64 MB or 96 MB tmpfs, filled with inserts. DEP-05.

## Symptom
Two different failures, depending on which file could not grow:

| What filled up | What PostgreSQL did | Data |
|---|---|---|
| A table file (`ERROR: could not extend file "base/...": No space left on device`) | The statement failed, its transaction rolled back, **the server stayed up** | All committed rows intact (100 of 100); writes worked again as soon as space was freed, no restart |
| The write-ahead log (`PANIC: could not write to file "pg_wal/xlogtemp...": No space left on device`) | WAL writer aborted, the server stopped instead of risking corruption; it could not restart while the disk was still full | On restart, crash recovery replayed the WAL (`redo starts ... redo done`). Restart needs at least one free WAL segment (16 MB): with only 12 MB freed it failed again |

The gateway sees either as PostgreSQL failing: requests keep working, request-log rows are buffered and then dropped with `fxa_request_log_dropped_total`, alert `FxaRequestLogDropping` (DEP-02). Keys keep working from the in-memory snapshot (ADR-025).

## Detection
- PostgreSQL log: `No space left on device`, `PANIC`.
- Gateway: `/readyz` reports `postgres` degraded; `fxa_dependency_failures_total{dependency="postgres"}`.
- Before it happens: `df` on the volume. On Kubernetes, kubelet_volume_stats_available_bytes where a CSI driver reports it (kind's local-path provisioner does not enforce sizes; documented, not drilled).

## Fix
1. Free space on the volume. Keep a **ballast file** (`dd if=/dev/zero of=<volume>/ballast bs=1M count=64`) on data volumes so there is always something safe to delete; it must be larger than a WAL segment (16 MB).
2. If PostgreSQL stopped, start it again; crash recovery replays the WAL by itself.
3. Then fix the cause: retention of `request_log` (it grows by one row per request), or a bigger volume.

## Not drilled
Qdrant and Redis with a full disk. Redis runs without persistence here, so it does not write to disk; Qdrant would fail upserts during ingestion (ingestion reports per-document failures, DAT-07) while searches keep working.
