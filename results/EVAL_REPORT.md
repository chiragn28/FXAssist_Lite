# Evaluation report (GPU lab)

Answer quality of the RAG agent with the real models on a free Kaggle GPU (ADR-016). Same evaluation set as every local run: 22 answerable or ambiguous questions, 11 that should not be answered (unanswerable, off-domain, vague), 16 safety items (4 advice, 2 reveal, 10 injection attacks) and 3 planted-excerpt scenarios: 52 items.

**Run:** 2026-10-07, Kaggle, Tesla T4 (compute capability 7.5), vLLM 0.31.0, `--dtype float16`, TRITON_ATTN attention backend, `max_model_len` 4096. Full corpus: 26 documents, 727 chunks, corpus version `b49a4f06efe11e68` (identical to every local ingestion). Raw files: `results/raw/20261007/fxassist_results/eval/` (per-item JSON, summaries, the fetch and ingest reports). Their `summary.txt` headers still say "local dev run": the label was fixed after this run (commit `69c73d6`).

## Results

| Measure | FP16 (Qwen2.5-3B-Instruct) | AWQ 4-bit (Qwen2.5-3B-Instruct-AWQ) | Local reference: Ollama Q4, laptop |
|---|---|---|---|
| Retrieval hit rate (answerable + ambiguous) | 22/22 (100%) | 22/22 (100%) | 22/22 |
| Answered (answerable + ambiguous) | 16/22 (73%) | 13/22 (59%) | 19/22 |
| Citation correctness (of answered) | 15/16 (94%) | 12/13 (92%) | 18/19 |
| Answer content checks (answerable) | 16/22 (73%) | 12/22 (55%) | not recorded |
| Abstention accuracy (should not answer) | 11/11 (100%) | 11/11 (100%) | 11/11 |
| Safety: advice, reveal, 10 injection attacks | 16/16 (100%) | 16/16 (100%) | 16/16 |
| Planted-excerpt scenarios (RET-02, RET-03, SAF-03) | 3/3 | 3/3 | 3/3 |
| **Overall** | **46/52 (88%)** | **42/52 (81%)** | 48/52 |
| Latency p50 / p95, questions reaching the model | 3.5 s / 6.7 s | 1.5 s / 2.4 s | 0.9 s / 1.5 s |

The local column is a development reference (`LEARNING.md`, Phase 1), not a GPU-lab result. Its model is a different quantisation (GGUF Q4_K_M), on a different GPU, under Ollama: compare it with care.

## What it says

- **Retrieval is not the bottleneck.** Every answerable question retrieved an expected document, with both models. All differences below come from generation.
- **Failures are abstentions, not wrong answers.** 15 of the 16 failed items are the model saying the documents do not answer the question, when they do. That is the safe direction: the validator rejects answers whose numbers are not in the cited text, so an uncertain model gives up instead of inventing. FP16 abstained on 6 answerable questions, AWQ on 9.
- **The hard questions are leverage limits.** a02 (ESMA, major pairs), a18 (Japan) and a21 (EU) failed for both models; the same questions flipped between pass and fail across local runs. The excerpts list several limits for different asset classes, and a 3B model struggles to pick the right one and cite it exactly.
- **AWQ is faster but answers less.** At these sample sizes AWQ halved latency (1.5 s vs 3.5 s p50, the same direction as the benchmark: `results/BENCHMARKS.md`) and answered 3 fewer questions. 3 of 22 is not a precise measurement, but it matches the expectation that 4-bit weights cost some reliability on exact, multi-number answers.
- **Safety held everywhere:** all advice requests declined by code, both prompt-extraction attempts refused, 10 of 10 injection attacks, and all three planted-excerpt scenarios (conflicting sources, an injected instruction inside an excerpt, an invented number).

## One answer that should have been rejected

AWQ answered a02 with: *"[ES2] ESMA sets a leverage limit of [S1] for retail clients on major currency pairs."* The model replaced the number with a citation label and invented the label `[ES2]`. `[S1]` is a real retrieved excerpt and the sentence contains no number to check, so the validator kept it and the eval counted a valid citation; only the content check (no "30:1") failed it. The answer is harmless but empty. The citation normaliser deliberately does not guess garbled labels like `[ES2]` (commit `af62e7b`). **Gap in the output checks:** a citation used *as* a value ("a limit of [S1]") is not detected. A fix would treat a citation directly after "of", "is", "at" or similar as a missing value and reject the answer.

## An earlier run that is not a result

The first full GPU run (`results/raw/20261007-v3-benchmark/`, notebook version 3) also ran this evaluation, and scored FP16 39/52 and AWQ 38/52 with a retrieval hit rate of only 15/22. **All 10 Wikipedia documents were missing from its index:** Wikimedia rejected the downloader's generic User-Agent. That run also used an earlier citation normaliser that rejected some citation formats vLLM produced (`[According to S1]`, `(S1)`). Both were fixed (commit `af62e7b`: a User-Agent with a contact URL, normalisation of unambiguous citation variants), and the evaluation was re-run: the numbers above. The fetch and ingest reports are now saved with every evaluation, so an incomplete corpus is visible in the results instead of hiding in a lower score.

## Cautions

- **Small samples.** 22 answerable questions: one question moves a rate by about 4.5 points. Differences of one or two items are noise.
- **One run per model.** Generation is greedy (temperature 0) but GPU arithmetic is not perfectly deterministic; a rerun can flip borderline items.
- **The checks are string-based.** "Content check" means the answer contains an expected fact (for example "30:1"); it does not judge fluency or completeness. No paid judge model was used (ADR-016).
- **A 3B model on a T4** is a small, cheap setup. These numbers say how this pipeline behaves with it, not what a larger model would score.
