# Runbook: LLM timeout storm (model server hangs or fails)

Written from a drill on 2026-10-07: compose stack with the mock LLM set to `{"hang": true}` (accepts requests, never answers) while `make load` sent mixed traffic with 3 workers for about 5 minutes. Rows: LLM-01, LLM-04; ADR-012, ADR-014.

## Symptom
- Requests that need the model wait about 30 s (`FXA_LLM_READ_TIMEOUT_S`), then get **504 `llm_timeout`**.
- After 5 consecutive failures the circuit breaker opens: requests get **503 `llm_circuit_open`** immediately, with `Retry-After`. Every 30 s one trial call is allowed (half-open); it times out and the breaker opens again.
- Questions already in the cache are still answered (200, `cached: true`). In the drill, 365 of 746 requests were still answered, almost all from the cache.
- `/readyz` reports `llm` not ok ("circuit open", or the probe timing out), so the instance is `not_ready`.

## Detection
What the drill showed, in order:

| After the model died | Signal |
|---|---|
| ~0 s | Model-call failure ratio jumps to 100% (`fxa_llm_calls_total{result!="ok"}`); panel "LLM calls by result" |
| ~1 min | Circuit breaker state 2 (open), then cycles 2 -> 1 -> 2 every 30 s; `fxa_llm_circuit_opened_total` climbs |
| ~2 min 15 s | Alert **FxaLLMFailing** fires (more than half of model calls fail for 2 min) |
| ~3 min | Alert **FxaLLMCircuitNotClosing** fires (breaker not closed once in 2 min) |

**What did not work (first version of the alerts):** "circuit open for 1 minute" never fired, because the breaker goes half-open every 30 s; "timeouts above 3 per minute" never fired, because the open breaker turns timeouts into fast `llm_circuit_open` errors; "5xx above 5% of requests" never fired, because the cache kept answering popular questions (5xx were about 2.5% of all requests). **Alert on the dependency's calls, not on user requests, when a cache and a breaker sit in between.**

## Root causes to check
1. Model server stuck or overloaded: `docker logs fxassist-ollama-1` (or vLLM logs), `ollama ps`. GPU memory full? (`nvidia-smi`; see `gpu-oom.md` and `kv-cache-exhaustion.md` from Phase 6.)
2. Model not loaded / still loading after a restart (a cold load of a 3B model takes seconds; a 7B model on a T4 much longer).
3. Network between gateway and model server (`docker compose exec gateway python -c "import urllib.request as u; print(u.urlopen('http://ollama:11434/v1/models', timeout=3).read()[:200])"`).
4. Prompts suddenly much longer (context near the window): check `fxa_context_truncations_total` and TTFT.

## Fix
- Restart or fix the model server. Nothing needs restarting in the gateway: the breaker's next half-open trial succeeds and closes it.
- In the drill: healed at 20:26:11; first request answered at 20:26:21 (the half-open trial), breaker closed by 20:26:32, both alerts resolved by 20:26:54 (**43 s**).
- On Kubernetes (Phase 4) the watchdog does the restart: N failed canary prompts, then a rollout restart of the model deployment, with a cooldown.

## Prevention and tuning
- Timeouts: 30 s silence or 60 s total per model call (`FXA_LLM_READ_TIMEOUT_S`, `FXA_LLM_TIMEOUT_S`). Shorter fails faster but cuts off slow long answers.
- Breaker: opens after 5 consecutive failures, stays open 30 s (`FXA_LLM_BREAKER_FAILURES`, `FXA_LLM_BREAKER_RESET_S`).
- Keep the answer cache on in production: during this outage it answered about half the traffic.
- Bounded queue (`FXA_MAX_CONCURRENT_RUNS`, `FXA_QUEUE_TIMEOUT_S`): requests stuck on a hanging model can only fill 4 slots; the rest get a fast 503 instead of piling up.
