# Evaluation report (GPU lab)

**PENDING.** The evaluation set has only been run locally with a 4-bit 3B model through Ollama (development numbers in `LEARNING.md`, Phase 1); those are not results (ADR-016).

To produce it: the notebook's evaluation stage (`RUN_EVAL = True`) runs `fxassist ingest` and `fxassist eval` against vLLM for the FP16 and AWQ models and saves `fxassist_results/eval/<variant>/` (per-item JSON and the summary). This report will then give, per variant: retrieval hit rate, citation correctness, abstention accuracy, safety (advice, reveal and injection items), sample sizes, and the cautions that 22 answerable questions allow (one question moves a rate by about 5 points).
