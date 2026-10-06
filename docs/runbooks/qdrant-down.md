# Runbook: Qdrant down

Written from the drill `make drill` (2026-10-06, compose stack, `docker compose stop qdrant`). DEP-01.

## Symptom
- `POST /v1/ask` returns **503** with `{"error": {"code": "store_unavailable", ...}}` and `Retry-After: 5`.
- `GET /readyz` returns **503** `not_ready`, with `checks.qdrant.ok = false`.
- Gateway log: `ERROR fxassist_agent.graph ... vector store failure: Vector search failed (ResponseHandlingException). Is Qdrant running?`

What does **not** happen: no answer is produced without documents. The model is never called (test_dep01_qdrant_down_gives_503_and_never_calls_the_model).

## Detection
- Readiness fails, so Kubernetes stops routing to the gateway (Phase 4) and compose shows the gateway not ready in `make ps` via `/readyz`.
- Metric `fxa_dependency_failures{dependency="qdrant"}` and `fxa_errors{code="store_unavailable"}` (dashboards and alerts in Phase 3).

## Root causes seen or likely
1. Container stopped or crashed (`docker compose ps qdrant`, `docker logs fxassist-qdrant-1`).
2. Out of memory: Qdrant has `mem_limit: 512m`. Check `docker inspect fxassist-qdrant-1 --format '{{.State.OOMKilled}}'`.
3. Collection missing (fresh volume): different code, `index_not_ready`, message says `make ingest`.
4. Collection built with another embedding model: `index_not_ready` with the model mismatch (DAT-10).

## Fix
```bash
make ps                          # is qdrant running and healthy?
docker compose --project-directory . -f deploy/compose/compose.yaml --env-file .env start qdrant
curl -s localhost:8000/readyz    # expect "ready" within seconds
make ingest                      # only if the collection is missing (index_not_ready)
```
No gateway restart is needed: in the drill it was ready again **1.1 s** after Qdrant started.

## Prevention
- Keep the healthcheck and `restart: unless-stopped` (already set).
- Watch Qdrant memory against its limit; raise `mem_limit` before the corpus grows.
- Data lives in the `qdrant_data` volume, so a restart loses nothing; `make down-volumes` does delete it.
