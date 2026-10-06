# Benchmarks

**PENDING.** No GPU run has happened yet. Nothing in this file is estimated.

To produce it (Phase 6, about 4 to 5 GPU hours, `docs/KAGGLE_PLAYBOOK.md`):

1. `make lab-dry-run` on your laptop (the same code against the mock LLM).
2. Run `notebooks/fxassist_gpu_lab.ipynb` on Kaggle with a T4 (or T4 x2), Internet on.
3. Download `fxassist_results.zip` from the notebook output.
4. `unzip` it into `results/raw/<date>/` and run `make report RUN=results/raw/<date>/fxassist_results`. That command rewrites this file with: FP16 vs AWQ at concurrency 1/4/16/32 for short and long prompts; the four knobs one at a time; GPU memory and KV-cache size per server from vLLM's own log; the experiment log. Method: `docs/BENCHMARK_METHOD.md`.
