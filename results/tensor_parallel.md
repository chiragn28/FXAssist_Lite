# Tensor parallelism on 2 x T4 (Phase 7)

**PENDING.** Not run yet.

The notebook's `tensor-parallel-2` experiment (priority 7) serves the FP16 model with `--tensor-parallel-size 2` and runs concurrency 1/16/32 x short/long, to compare with the single-GPU baseline: same model, prompts and settings, one variable. It needs Kaggle's **GPU T4 x2** accelerator:

- one GPU assigned: the experiment is skipped and recorded in `stages.jsonl` (GPU-07)
- the server fails to start (for example an NCCL error, since the two T4s are connected over PCIe without NVLink): the error is recorded verbatim, the lab retries once with `NCCL_P2P_DISABLE=1`, and records that outcome too (GPU-08)

`make report` puts the TP=1 vs TP=2 table in `results/BENCHMARKS.md`; this file will hold the write-up: what ran, the numbers, and the exact errors if it did not.
