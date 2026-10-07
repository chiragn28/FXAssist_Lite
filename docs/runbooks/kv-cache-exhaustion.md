# Runbook: KV cache exhaustion vs request queueing (vLLM)

Written from the GPU lab on 2026-10-07 (Kaggle, Tesla T4, vLLM 0.31.0, Qwen2.5-3B-Instruct; vLLM metrics sampled every 10 s into `samples.jsonl.gz`).

**What actually happened:** the KV cache **never** ran out. Across every server, peak usage was 21% and there were 0 preemptions. But first-token times still grew to 17 s (p95) with 32 users on long prompts, and up to 24 requests waited. This runbook is mostly about telling the two causes apart, because "the KV cache is full" is the usual first guess and here it was wrong.

## Background

The KV cache holds what the model has already read for each running request (36 KiB per token for this model in FP16; see `gpu-oom.md`). When it fills, vLLM **preempts** a running request (frees its cache and recomputes it later), and new requests wait until space is free.

## Symptom

Both causes look the same from outside: time to first token grows with load, requests wait, throughput flattens.

## Detection: read these three metrics together

| Metric (Prometheus, from vLLM's `/metrics`) | KV cache exhaustion | Prefill or scheduler queueing |
|---|---|---|
| `vllm:kv_cache_usage_perc` | near 1.0 (100%) | well below 1.0 |
| `vllm:num_preemptions_total` | rising | flat |
| `vllm:num_requests_waiting` | rising | rising |

Measured on 2026-10-07:

| Server | Peak KV usage | Preemptions | Peak waiting |
|---|---|---|---|
| baseline-fp16 | 20.9% | 0 | 20 |
| baseline-awq | 13.9% | 0 | 24 |
| tensor-parallel-2-fp16 | 7.4% | 0 | 24 |
| max-num-seqs-8-fp16 | 5.6% | 0 | 11 |

Waiting with the cache at 21% and zero preemptions means **queueing, not exhaustion**.

## Root causes seen

1. **Concurrency cap.** With `--max-num-seqs 8`, only 8 requests run; the rest queue. At 16 users, time to first token rose from 0.29 s to 4.57 s (short prompts) and throughput fell from 408 to 228 tokens/s, while the KV cache was 6% full.
2. **Prefill budget (inferred).** With 32 users sending about 1,500-token prompts at once, first-token p95 reached 17 s at the default settings. Each scheduler step processes a bounded number of prompt tokens (the log shows a compile range of 1 to 2,048 tokens, so about two long prompts per step), so the last prompt waits for the others. This is an inference from the logs; the exact `max_num_batched_tokens` value was not recorded.

## Fix

- **Exhaustion** (cache near full, preemptions rising): more KV memory (raise `--gpu-memory-utilization`, use the AWQ build, which gave 1.5x the KV tokens here, or `--tensor-parallel-size 2`, which gave 2.8x), shorter `--max-model-len`, or fewer concurrent requests.
- **Concurrency cap**: raise `--max-num-seqs` if the KV cache has room.
- **Prefill queueing**: a larger per-step token budget (`--max-num-batched-tokens`) trades longer steps for less waiting; prefix caching removes repeated prefill (TTFT 3.30 s to 0.20 s here, in a best case with 96% cache hits); more GPUs.

## Prevention

- Alert on `vllm:num_preemptions_total` increasing and on `vllm:kv_cache_usage_perc > 0.9` for minutes, not on waiting requests alone.
- Size the cache: concurrent requests x (prompt + output tokens) x 36 KiB must fit in the KV memory the start-up log reports.

## A drill that will exhaust the cache (not run yet)

FP16 with `--gpu-memory-utilization 0.50`: a budget of 7.5 GiB, minus 5.79 GiB of weights, about 0.74 GiB of activations and overhead and 0.11 GiB of CUDA graphs (all measured at 0.90), leaves about 0.86 GiB of KV cache, roughly 25,000 tokens. 32 long requests (about 1,650 tokens each with output) need about 53,000 tokens, so vLLM must preempt. Going lower (0.45) would leave too little for even one 4,096-token sequence, and vLLM would refuse to start instead: that is the `gpu-oom.md` case. Watch the three metrics above while it runs and record them here.
