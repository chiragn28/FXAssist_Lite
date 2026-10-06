# bench

Benchmark harness for vLLM following ADR-017: cache off, warm-up discarded, fixed prompt file with its hash recorded, at least 3 repetitions, p50 and p95, one variable changed at a time. Every result row is written to disk immediately so a killed session can resume.

Built in **Phase 5**, run in **Phases 6 and 7**. Edge cases: BEN-*, CAC-05, GPU-05.
