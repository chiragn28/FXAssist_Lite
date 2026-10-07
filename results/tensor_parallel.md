# Tensor parallelism on 2 x T4 (Phase 7)

**Ran successfully** on 2026-10-07 (Kaggle notebook version 3, accelerator "GPU T4 x2"). Raw files: `results/raw/20261007-v3-benchmark/` (`benchmark.jsonl`, `server-logs/tensor-parallel-2-fp16.log`).

## What ran

Qwen2.5-3B-Instruct in FP16 served with `--tensor-parallel-size 2` (each layer's weights split across the two GPUs, which combine partial results after every layer), compared with the single-GPU FP16 baseline: same model, prompts, `max_tokens` 128 with `ignore_eos`, prefix caching off, 3 repetitions per cell. Only the tensor-parallel size changed (BEN-07).

## Results (median of 3 repetitions)

| Prompts | Concurrency | TP=1 tokens/s | TP=2 tokens/s | Speed-up | TTFT p50 TP=1 / TP=2 (s) |
|---|---|---|---|---|---|
| short | 16 | 408 | 694 | 1.70x | 0.29 / 0.20 |
| short | 32 | 629 | 1,006 | 1.60x | 0.54 / 0.30 |
| long | 1 | 18 | 49 | 2.7x (see caution) | 0.66 / 0.37 |
| long | 16 | 121 | 220 | 1.82x | 3.30 / 1.80 |
| long | 32 | 135 | 245 | 1.81x | 3.66 / 2.17 |

Memory per GPU with TP=2: 2.94 GiB of weights (half of 5.79 GiB), leaving room for a KV cache of 564,928 tokens across both GPUs, against 199,744 on one GPU.

No errors in any cell (error rate 0%).

## How the GPUs talked to each other (GPU-08)

From the server log:

- `Custom allreduce is disabled because your platform lacks GPU P2P capability or P2P test failed.` The two T4s cannot read each other's memory directly (no NVLink, no PCIe peer-to-peer on this host).
- `Using ['PYNCCL'] all-reduce backends` and `vLLM is using nccl==2.29.7`: vLLM fell back to NCCL, which passes data through host memory.
- `FlashInfer All Reduce is disabled because it is not supported for world_size=2.`

So the feared NCCL / peer-to-peer problem did not stop anything: vLLM detected it and chose a slower but working path by itself. No environment workaround (such as `NCCL_P2P_DISABLE=1`) was needed, and the lab's retry with it was never triggered.

## Reading the numbers

- **1.6x to 1.8x, not 2x, under load.** Two GPUs double the memory bandwidth and compute, but after every layer the GPUs must combine their results over PCIe through the host. That communication is pure overhead. Long prompts scale slightly better (1.8x) than short ones (1.6 to 1.7x) because they do more computation per byte communicated.
- **Caution on the single-user 2.7x.** Doubling memory bandwidth alone predicts at most about 2x. The single-GPU FP16 single-user figures look low in this run (about 40% of the T4's bandwidth limit, and one neighbouring cell was hit by what looks like clock throttling, see `results/BENCHMARKS.md` section 5), so 2.7x probably overstates the real gain. The 16- and 32-user ratios are the reliable comparison.
- **Why bother on a 3B model?** It fits on one T4 comfortably. Tensor parallelism matters when a model does not fit on one GPU, or when single-request latency matters more than cost: here it cut first-token time by 30 to 45% at the price of a second GPU.

## Limits

- One run on Kaggle's shared hardware; T4s connected over PCIe through the host. Results on GPUs with NVLink (A100, H100) would scale better and are not comparable.
- Startup with TP=2 took about 100 s (two workers, each compiling and capturing CUDA graphs) against 33 to 104 s for one GPU.
