# Runbook: Redis down

Written from the drill `make drill` (2026-10-06, compose stack, `docker compose stop redis`). CAC-01, ADR-010.

## Symptom
Mostly none for users, by design:
- Requests still succeed (**200**), but nothing is served from the cache (`"cached": false` for repeats) and answers take full model time.
- Rate limiting switches to a **per-process in-memory bucket 4x stricter** than normal (`FXA_RATE_LIMIT_FALLBACK_DIVISOR`). Heavy users may see 429 sooner.
- `GET /readyz` returns **200** `degraded`, with `checks.redis.ok = false`.
- Gateway log, once per 5 s at most: `WARNING fxassist_gateway.redis_state ... Redis failed (...); skipping it for 5s`.

## Detection
- `/readyz` status `degraded`.
- Metrics: `fxa_cache_lookups{result="skip"}` rises, `fxa_rate_limit_decisions{backend="memory"}` appears, `fxa_dependency_failures{dependency="redis"}` (dashboards in Phase 3).
- Model load rises because every request is a cache miss.

## Root causes seen or likely
1. Container stopped or restarting.
2. Memory limit: Redis runs with `--maxmemory 96mb` and `volatile-lru`; it evicts cache entries rather than failing, so OOM here points at the container limit (128m).

## Fix
```bash
docker compose --project-directory . -f deploy/compose/compose.yaml --env-file .env start redis
curl -s localhost:8000/readyz    # expect "ready"
```
The gateway retries Redis at most every 5 s, so it recovers by itself: in the drill it was ready **3.4 s** after Redis started. The cache starts empty (no persistence, on purpose), so expect a burst of cache misses.

## Why it is designed this way
- **Cache fails open:** a cache is an optimisation; losing it must not lose the service.
- **Limiter falls back to stricter local limits, not to none:** an outage must not mean unlimited traffic to the model. With N gateway replicas the total is N x (limit / 4), which is still bounded.
- **Short timeouts (250 ms) and a 5 s skip window:** without them every request would wait on a dead Redis.
- **After a restart buckets are full again (CAC-06):** users may get one extra burst. Allowing slightly more is preferred over locking everyone out.
