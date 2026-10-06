# mock_llm

A configurable OpenAI-compatible server used in CI and on kind instead of a real model (ADR-003). It must be able to misbehave: added latency, slow first token, HTTP errors (429/503), empty answers, malformed chunks and mid-stream disconnects (LLM-09).

Built in **Phase 2**.
