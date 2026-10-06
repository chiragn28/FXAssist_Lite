# gateway

FastAPI service in front of everything: `POST /v1/ask` with SSE streaming, hashed API keys in PostgreSQL, token-bucket rate limiting and answer cache in Redis, request IDs, `/healthz` and `/readyz`, the standard informational disclaimer, and the LLM adapter (timeouts, retries, circuit breaker).

Built in **Phase 2**. Edge cases: API-*, CAC-*, LLM-*, DEP-01/02/04, SAF-06.
