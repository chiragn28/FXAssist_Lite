# gateway

The FastAPI service in front of everything (Phase 2). Package `fxassist_gateway`, command `fxassist-gateway`.

## Endpoints

| Endpoint | Auth | What |
|---|---|---|
| `POST /v1/ask` | API key | `{"question": "...", "stream": true}`. SSE by default, JSON with `"stream": false` |
| `GET /v1/info` | API key | model, cache on/off, prompt version (the benchmark guard reads it, CAC-05) |
| `GET /healthz` | none | the process is alive |
| `GET /readyz` | none | `ready` / `degraded` (200) or `not_ready` / `shutting_down` (503), with each check |

Keys: `Authorization: Bearer fxa_...` or `X-API-Key: fxa_...`. Create one with `make api-key NAME=alice`; it is printed once and takes effect within 30 s (ADR-025).

## Streaming (ADR-024)

```
event: meta     {"request_id": "...", "cached": false}
event: status   {"stage": "queued" | "guard" | "retrieve" | "grade" | "rewrite" | "generate" | "validate"}
: keep-alive                      (comment, every 10 s while waiting)
event: answer   {"outcome", "answer", "citations", "disclaimer", "cached", "coalesced", ...}
event: done     {}
```
or `event: error {"code", "message", "status", "request_id"}`. The answer text is sent only after citation and number validation.

## Errors

| HTTP | `code` | When |
|---|---|---|
| 401 | `unauthorized` | any key problem; the message never says which (API-01) |
| 413 | `payload_too_large` | body over 16 KiB |
| 422 | `invalid_request`, `invalid_json`, `invalid_encoding`, `question_too_long` | API-03 |
| 429 | `rate_limited` | with `Retry-After` (API-02) |
| 502 | `llm_empty`, `llm_bad_response` | LLM-02, LLM-03 |
| 503 | `store_unavailable`, `index_not_ready`, `llm_unavailable`, `llm_circuit_open`, `busy`, `auth_unavailable`, `shutting_down` | with `Retry-After` |
| 504 | `llm_timeout`, `time_limit` | LLM-01 |

## Modules

| Module | Job |
|---|---|
| `app.py` | Request flow, components, SSE event stream |
| `auth.py` | Key format, hashing, snapshot verification (API-01, ADR-025) |
| `redis_state.py` | Fail-open guard, token bucket (Lua), answer cache (ADR-010, CAC-01/06) |
| `answers.py` | Cache key, TTL policy, response payload (CAC-02/03/04, SAF-06) |
| `runner.py` | Thread pool with a bounded queue; request coalescing (API-04, LLM-05) |
| `sse.py` | SSE response with write timeout and disconnect detection (API-05/07) |
| `middleware.py` | Request IDs and draining (API-06/08) |
| `db.py`, `logwriter.py` | PostgreSQL schema, keys, non-blocking request log (DEP-02) |
| `health.py` | Readiness checks (ADR-014) |
| `metrics.py` | OpenTelemetry instruments (exported from Phase 3) |
| `cli.py` | `serve` with graceful shutdown, key management |

The LLM adapter (timeouts, retries, circuit breaker, stream normalisation) lives in `fxassist_agent.llm`, so `make ask` and `make eval` use the same code path as the API.

## Tests

`services/gateway/tests`: the gateway runs under uvicorn on a free port with fakeredis, in-memory key and log stores, in-memory Qdrant and the mock LLM, each of which a test can break. `make drill` repeats the dependency failures against the real containers.
