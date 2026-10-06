# mock_llm

A configurable OpenAI-compatible server used in tests, drills, CI and on kind instead of a real model (ADR-003, LLM-09). Package `fxassist_mock_llm`, command `fxassist-mock-llm` (port `FXA_MOCK_PORT`, default 8080; in compose it is published on `FXA_MOCK_LLM_PORT`, default 8081).

Its answers are RAG-aware, so the whole agent works against it: grader calls mark every excerpt relevant, answer calls quote the first sentence of excerpt S1 with a citation.

## Misbehaviour

Set at startup with `FXA_MOCK_CONFIG` (JSON) or at runtime:

```bash
curl -X POST localhost:8081/_mock/config -d '{"error_status": 503, "error_count": 2, "retry_after": 1}'
curl localhost:8081/_mock/stats
curl -X DELETE localhost:8081/_mock/config      # back to normal, stats reset
```

| Field | Effect |
|---|---|
| `latency_ms` | delay before the response starts |
| `first_token_ms`, `token_ms` | slow first token, delay between chunks |
| `error_status` + `error_count` / `error_rate`, `retry_after` | HTTP errors (e.g. 429, 503), N times or at random |
| `empty` | an answer with no content |
| `malformed_every` | an unparseable chunk before every Nth chunk |
| `disconnect_after` | drop the connection after N chunks |
| `hang` | never answer (until the client leaves) |
| `flavour` | `openai`, `ollama` (copied from real Ollama output), `vllm`, `quirky` (CRLF, no space after `data:`, comments, `event:` lines, null content) |
| `reply` | fixed answer text |
| `models` | model names it serves; others get 404 like Ollama |

Stats include `streams_cancelled`, which tests use to prove a client disconnect really stopped generation (LLM-05).
