## What the numbers say (hand-written analysis)

Run: Kaggle notebook version 3, 2026-10-07, 2 x Tesla T4 (15,360 MiB, compute capability 7.5), vLLM 0.31.0, PyTorch 2.13.0, CUDA 13.0, attention backend TRITON_ATTN, Qwen2.5-3B-Instruct FP16 and its official AWQ 4-bit build, `max_tokens` 128 with `ignore_eos` (every request generates exactly 128 tokens), 3 repetitions per cell, cache and prefix caching off unless stated. Raw files: `results/raw/20261007-v3-benchmark/`.

### 1. AWQ beats FP16 on a T4, by less as load grows (BEN-09)

| Prompts | Concurrency | FP16 tokens/s | AWQ tokens/s | AWQ / FP16 |
|---|---|---|---|---|
| short | 4 | 123 | 284 | 2.3x |
| short | 16 | 408 | 797 | 2.0x |
| short | 32 | 629 | 1,094 | 1.7x |
| long | 1 | 18 | 50 | 2.8x |
| long | 16 | 121 | 138 | 1.14x |
| long | 32 | 135 | 144 | 1.07x |

- **Why AWQ wins at low load:** generating a token means reading every weight once. AWQ weights take 1.95 GiB instead of 5.79 GiB, so each step moves about a third of the data, and the T4 is limited by memory bandwidth here.
- **Why the gap closes with long prompts and many users:** the work becomes prefill (reading thousands of prompt tokens), which is limited by arithmetic, not memory. AWQ also has to unpack its 4-bit weights, so it gains little. Time to first token is the same for both on long prompts (3.66 s vs 3.76 s p50 at 32 users).
- **AWQ also frees memory for the KV cache:** 10.32 GiB / 300,496 tokens vs 6.86 GiB / 199,744 tokens (1.5x more concurrent context).
- Answer quality differences belong to the evaluation, not here (`results/EVAL_REPORT.md`).

### 2. One knob at a time, FP16 at 16 users (BEN-07)

| Change | Effect | Why |
|---|---|---|
| Prefix caching on | Long prompts: TTFT p50 3.30 s to 0.20 s, throughput 121 to 290 tokens/s; short: TTFT 0.29 s to 0.14 s | vLLM reuses the KV cache of prompt text it has already processed. The long prompt set reuses a handful of 1,500-token context blocks and the same prompts recur across repetitions, so **96% of prompt tokens hit the cache** (241,040 of 250,959, from vLLM's counters). This is a best case: in real RAG traffic the retrieved excerpts differ per question and would hit far less. It is also why prefix caching is off in every other cell (BEN-02) |
| `max_num_seqs` 32 to 8 | TTFT 0.29 s to 4.57 s (short), throughput 408 to 228 tokens/s | Only 8 requests may run at once; the other 8 wait in a queue. The clearest picture of queueing in this run |
| `gpu_memory_utilization` 0.90 to 0.80 | KV cache 199,744 to 157,328 tokens; no measurable speed change | The KV cache never filled (peak usage 21%, see 4), so a smaller one cost nothing at this load |
| `max_model_len` 4,096 to 2,048 | No measurable change | Prompts here are far shorter than either limit |

### 3. Tensor parallelism across both T4s (Phase 7)

See `results/tensor_parallel.md`. Summary: 1.6x to 1.8x the throughput of one GPU at 16 and 32 users; it ran first time over PCIe with NCCL (no peer-to-peer, no errors).

### 4. Where the waiting comes from: not the KV cache

From vLLM's own metrics, sampled every 10 seconds (`samples.jsonl.gz` in the raw folder):

| Server | Peak KV cache usage | Preemptions | Peak requests waiting |
|---|---|---|---|
| baseline-fp16 | 20.9% | 0 | 20 |
| baseline-awq | 13.9% | 0 | 24 |
| tensor-parallel-2-fp16 | 7.4% | 0 | 24 |
| max-num-seqs-8-fp16 | 5.6% | 0 | 11 |

Up to 24 requests waited while the KV cache was at most 21% full and nothing was ever preempted. So the long first-token times with 32 users on long prompts (TTFT p95 17 s) are **prefill queueing**, not KV-cache exhaustion. The likely limit is the per-step token budget (the server log shows a compile range of 1 to 2,048 tokens): each step can prefill only about two long prompts. This is an inference from the logs, not a measured setting. See `docs/runbooks/kv-cache-exhaustion.md` for telling the two apart.

### 5. One unreliable cell: FP16, 1 user, short prompts

The table above reports it as measured (10 tokens/s, range 10 to 18), but **do not use it for comparisons**. During its second and third repetitions:

- vLLM's own generation rate, with exactly one request running, slid from 26.4 to 18.7 to 9.1 tokens/s within about a minute and stayed near 9 for five minutes;
- GPU utilisation stayed at 100% the whole time.

Constant full load with steadily falling speed is what clock throttling (power or temperature limits on a passively cooled T4) looks like. It is **not proven**: the lab sampled GPU memory and utilisation, not clocks, temperature or power. The FP16 single-user rate on long prompts (about 20 tokens/s decode, three steady repetitions) is the better single-stream FP16 figure. Next session: sample `clocks.sm`, `temperature.gpu`, `power.draw` and `clocks_throttle_reasons.active` with the other metrics, and rerun this cell.

### 6. Server start-up time

| Start | Ready after | `torch.compile` |
|---|---|---|
| Cold: new model or new `max_model_len` / `max_num_seqs` | 77 to 104 s | 22 to 27 s |
| Warm: same settings as an earlier start (compile cache reused) | about 33 s | 0.2 s |

Loading the weights themselves took 1 to 6 seconds. See `docs/runbooks/slow-model-load.md`.

### 7. The out-of-memory drill did not run out of memory

Settings meant to fail (`gpu_memory_utilization` 0.99, `max_model_len` 32,768, `max_num_seqs` 256) started normally: vLLM sized the KV cache to what was left (7.32 GiB, 213,168 tokens, 6.5 concurrent sequences of 32,768 tokens) and served. See `docs/runbooks/gpu-oom.md` for why, and for a drill that will fail.

### Cautions

- T4 numbers are not comparable to A100/H100 numbers (ADR-017).
- One run, one machine, three repetitions per cell; Kaggle shares hardware and the throttling episode shows conditions can change mid-run.
- Fixed 128-token outputs make throughput comparable across cells but are shorter than many real answers.
