# Benchmark method

How the GPU-lab numbers are produced and how to read them (ADR-017). Code: `bench/harness.py` (measurement), `bench/matrix.py` (what is run), `bench/report.py` (tables). Edge cases BEN-01 to BEN-09.

## What is measured

The harness sends chat completions directly to the vLLM server, **not** through the gateway: no answer cache, no rate limit, no retrieval, so the numbers describe the model server (BEN-02, CAC-05; the gateway path refuses benchmarks with its cache on via `bench/guard.py`).

| Metric | Definition |
|---|---|
| TTFT | Time from sending the request to the first streamed chunk with content. Includes queueing inside vLLM and prompt processing (prefill). |
| Latency | Time from sending the request to the end of the stream. |
| Decode tokens/s | Per request: (output tokens - 1) / (latency - TTFT). Speed after the first token. |
| Throughput | All output tokens of the successful requests / wall-clock time of the repetition. What the server delivers under that load. |
| Requests/s | Successful requests / wall-clock time. |
| Error rate | Failed requests / all requests. Failures (non-200, broken stream, timeout, no content) are counted and listed by type, and are **left out of latency statistics**, so errors never look like fast successes (BEN-06). |
| GPU memory | vLLM's own startup log (`Model loading took`, `Available KV cache memory`, `KV cache size`), plus `nvidia-smi` and `/metrics` samples every 10 s in `samples.jsonl`. |

p50 and p95 are reported, never only an average (BEN-05). p95 uses the nearest-rank method: with 12 requests it is the 12th value, so it is the maximum. Read p95 of small cells as "the slowest request".

## Controls

| Rule | How |
|---|---|
| Warm-up discarded (BEN-01) | Each repetition first sends a few requests whose results are thrown away (CUDA graphs, caches, connection setup). |
| Cache off; prefix caching explicit (BEN-02) | The gateway is not involved. vLLM's prefix caching is **off** in every experiment except the one that measures it; the setting is written into every row. |
| Repetitions (BEN-03) | 3 repetitions per cell. Tables show the median and the min-max range. A range that overlaps another cell's range means "no measurable difference". |
| Fixed prompts (BEN-04) | Two sets built deterministically from files in the repo (`bench/prompts.py`): *short* (the 22 evaluation questions) and *long* (each question after ~1,500 tokens of context, RAG-sized). Their SHA-256 is in every row. |
| Fixed output length (BEN-08) | `max_tokens 128` with vLLM's `ignore_eos`, so every request generates exactly 128 tokens. The smoke test checks `ignore_eos` works. Token counts come from the server's `usage`. |
| One variable at a time (BEN-07) | Every knob experiment differs from the FP16 baseline in exactly one server setting; `check_one_variable` enforces it before anything runs. |
| Fair FP16 vs AWQ (BEN-09) | Same prompts, same load cells, same server settings; only the model repository (and `--quantization awq`) differ. Separate server runs. |
| Seeds | Temperature 0 and a seed per request. Throughput does not depend on the text, but fixed seeds keep the generated length and content repeatable. |

## The matrix

| Experiment | Server change | Cells |
|---|---|---|
| baseline / fp16 | none (`float16`, `max_model_len 4096`, `gpu_memory_utilization 0.90`, `max_num_seqs 32`, prefix caching off) | concurrency 1, 4, 16, 32 x short, long |
| baseline / awq | model: the AWQ repository | the same 8 cells |
| prefix-caching-on | `enable_prefix_caching: true` | concurrency 16 x short, long |
| max-num-seqs-8 | `max_num_seqs: 8` | the same |
| gpu-mem-0.80 | `gpu_memory_utilization: 0.80` | the same |
| max-model-len-2048 | `max_model_len: 2048` | the same |
| tensor-parallel-2 (Phase 7) | `tensor_parallel_size: 2` | concurrency 1, 16, 32 x short, long |

Requests per repetition: 4 x concurrency, at least 12.

## How to read the results

- **Concurrency curve:** throughput should rise with concurrency until the GPU is saturated or the KV cache is full; after that, latency rises without more throughput. Where that happens is the useful number.
- **Short vs long:** long prompts move cost into prefill, which shows up in TTFT. Prefix caching only helps when prompts share a prefix: the long set reuses context blocks across questions, the short set does not.
- **AWQ:** less memory for weights means more KV cache, so higher concurrency before saturation; per-token speed can go either way on a T4. Answer quality is compared separately with the evaluation set (`results/EVAL_REPORT.md`).
- **Small samples:** 3 repetitions show stability, not statistical significance. Differences smaller than the min-max ranges are not claimed.
- **Scope:** a free T4 is not an A100 or H100, and Kaggle's machines are shared. These numbers support statements about *relative* effects on this hardware, not absolute production capacity.
