# Runbook: GPU out of memory (vLLM)

Written from the GPU lab on 2026-10-07 (Kaggle, Tesla T4 15,360 MiB, vLLM 0.31.0, Qwen2.5-3B-Instruct). GPU-03.

**What actually happened:** the planned drill did **not** run out of memory. Settings chosen to fail (`--gpu-memory-utilization 0.99 --max-model-len 32768 --max-num-seqs 256`) started and served normally. That is the main lesson of this runbook: vLLM does not grab memory as it goes, it plans a budget at start-up, so an out-of-memory problem shows up **at start-up as a refusal**, not as a crash under load. A drill that does fail is described at the end; it has not been run yet.

## How vLLM spends GPU memory (measured)

At start-up vLLM takes `gpu_memory_utilization x total memory` as its budget, loads the weights, runs a profiling pass to measure activation memory, captures CUDA graphs, and gives **everything left** to the KV cache. From the server logs:

| Server (one T4) | Budget | Weights | CUDA graphs | KV cache | KV tokens |
|---|---|---|---|---|---|
| FP16, utilisation 0.90 | 13.5 GiB | 5.79 GiB | 0.11 GiB | 6.86 GiB | 199,744 |
| FP16, utilisation 0.80 | 12.0 GiB | 5.79 GiB | 0.11 GiB | 5.40 GiB | 157,328 |
| FP16, utilisation 0.99 (the drill) | 14.85 GiB | 5.79 GiB | 0.36 GiB | 7.32 GiB | 213,168 |
| AWQ, utilisation 0.90 | 13.5 GiB | 1.95 GiB | 0.09 GiB | 10.32 GiB | 300,496 |

The rest of each budget (about 0.7 GiB) is activations and runtime overhead measured by the profiling pass.

**KV cache per token** for this model: 2 (key and value) x 36 layers x 2 KV heads x 128 dimensions x 2 bytes = **36 KiB**. Every row above matches it to within 0.01 KiB (6.86 GiB / 199,744 tokens = 36.0 KiB). The KV cache is FP16 even for the AWQ model: quantising the weights does not shrink it.

## Why the drill started anyway

- 0.99 x 15 GiB left 7.32 GiB after weights and graphs: room for 213,168 tokens.
- `max_model_len 32768` only requires that **one** sequence of 32,768 tokens fits (it does, 6.5 times over).
- `max_num_seqs 256` is a ceiling, not a reservation; vLLM queues requests instead of over-allocating.
- Nothing else was on the GPU. `nvidia-smi` showed `VLLM::EngineCore 14380MiB`.
- vLLM even reported that with CUDA-graph memory profiling on, 0.99 behaves like 0.9617.

## Symptom (when it does happen)

- **At start-up** (the usual case): the server exits before "Application startup complete", with an error that the model or the KV cache for `max_model_len` does not fit in the available memory. The lab records this in `stages.jsonl` with the error tail and an `nvidia-smi` snapshot (`lab.oom_drill`).
- **Under load**: rare with vLLM alone. Usually another process on the same GPU (a second server, a notebook cell holding tensors) took memory after vLLM made its plan: `CUDA out of memory` in the server log, requests failing with 5xx.

## Detection

- The server never becomes ready: readiness probes fail and the start-up timeout in `lab.Server.wait_ready` triggers (in Kubernetes: the startup probe, K8S-01).
- `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` shows who holds the memory.
- The log lines to read first: `Model loading took ... GiB`, `Available KV cache memory: ... GiB`, `GPU KV cache size: ... tokens`.

## Fix (in this order)

1. Something else on the GPU? Stop it, or give vLLM a smaller `--gpu-memory-utilization`.
2. Model too big for the budget: use the AWQ build (1.95 GiB instead of 5.79 GiB here), or split it across GPUs with `--tensor-parallel-size 2` (2.94 GiB per GPU here).
3. KV cache too small for `max_model_len`: lower `--max-model-len` to what the application needs (FXAssist needs about 4,096), or raise `--gpu-memory-utilization`.

## Prevention

- Budget before deploying: weights + about 0.7 GiB overhead + KV cache (36 KiB per token x context length x concurrent requests).
- One model server per GPU; nothing else allocating on it.
- Keep the start-up log lines above in the logs you ship, so the memory plan is visible after the fact.

## A drill that will fail (not run yet)

`--gpu-memory-utilization 0.35` with the FP16 model: the budget (0.35 x 15 GiB = 5.25 GiB) is smaller than the weights (5.79 GiB), so vLLM must refuse to start. Change `OOM_DRILL` in `bench/matrix.py` and rerun only the drill: `FXA_LAB_MAX_PRIORITY=6 FXA_LAB_RUN_EVAL=False make lab-push` (after lowering the other experiments' priorities, or with a dedicated priority). Record the exact error text here.
