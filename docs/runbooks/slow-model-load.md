# Runbook: slow model server start-up (vLLM)

Written from 11 real vLLM server starts in the GPU lab on 2026-10-07 (Kaggle, Tesla T4, vLLM 0.31.0, Qwen2.5-3B-Instruct FP16 and AWQ). Timings come from the server logs.

## Symptom

The model server takes one to two minutes to answer its first request after a (re)start. In Kubernetes this can look like a crash: the liveness probe kills a container that is still starting (K8S-01), and a restart loop follows because every restart starts from zero again.

## What the time is spent on (measured)

| Start | Ready after | Load weights | `torch.compile` | CUDA graph capture |
|---|---|---|---|---|
| Cold: first start, or new `max_model_len` / `max_num_seqs` / model | 77 to 104 s | 1 to 6 s | 22 to 27 s | 5 to 11 s |
| Warm: same model and settings as an earlier start | about 33 s | 1 to 6 s | 0.2 s | 5 to 6 s |

- **Loading the weights is the small part**: 5.6 s for the 5.79 GiB FP16 model, 1.0 s for the AWQ model, from local disk.
- **`torch.compile` is the big variable part.** vLLM compiles the model into optimised kernels and caches the result (`/root/.cache/vllm/torch_compile_cache`). The cache key includes the model and settings, so changing `max_model_len` or `max_num_seqs` meant a full recompile (22 s) even for the same model.
- **CUDA graph capture** records the GPU work for a set of batch sizes so each later step replays it cheaply. With `max_num_seqs 256` it captured 51 sizes (11 s) instead of 11 (5 s).
- **Tensor parallelism** started in about 100 s: two workers, each compiling and capturing.
- The very first download of the model is extra (seconds on Kaggle's network, minutes elsewhere) and is cached on disk after that.

## Detection

- The server log stops at compile or capture lines: `Compiling a graph for compile range ...`, `Capturing CUDA graphs ...`; `torch.compile took X s in total` and `Graph capturing finished in X secs` give the totals.
- The readiness endpoint (`/v1/models`) fails until `Application startup complete`.

## Fix

- **Don't let probes kill a starting server**: a startup probe with enough headroom (for this model, about 2 minutes cold) before liveness checks begin. The Helm chart's startup probe exists for exactly this (K8S-01).
- **Keep the compile cache across restarts**: put `~/.cache/vllm` on a persistent volume, so a restart with unchanged settings takes about 33 s instead of about 90 s.
- **Change settings rarely**: every change to `max_model_len` or `max_num_seqs` costs a cold compile on every replica.
- If start-up speed matters more than steady-state speed, `--enforce-eager` skips compilation and graph capture (not measured here; it makes every request slower).

## Prevention

- Measure start-up after every vLLM upgrade or settings change, and set the startup probe from the measurement plus a margin.
- During rollouts, keep old replicas serving until new ones are ready (readiness gates and a PodDisruptionBudget, K8S-07), so a slow start never becomes downtime.
