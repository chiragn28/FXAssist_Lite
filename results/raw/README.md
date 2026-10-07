# Raw GPU-lab output

Files exactly as the Kaggle notebook produced them; pre-commit's whitespace fixers skip this folder.

| Folder | Run | What it contains |
|---|---|---|
| `20261007-v3-benchmark/` | Kaggle notebook version 3, 2026-10-07 07:28-09:08 UTC, 2 x Tesla T4 | Everything: benchmarks (90 rows), tensor parallel, evaluation FP16 and AWQ, OOM drill, vLLM server logs. `fxassist_results.zip` is the original download |
| `20261007/` | An evaluation-only rerun the same day (`MAX_PRIORITY = 0`), after fixing what v3 exposed | Smoke test and evaluation with the full corpus. **This is the reported evaluation** (`results/EVAL_REPORT.md`). v3's own evaluation ran with all 10 Wikipedia documents missing from its index and is kept only as the record of that failure |
| `20261007-2223/` | The LoRA fine-tuning run (`make ft-push`, ADR-026) | Teacher data report, the agent runs that made the training data, the training log, and the evaluation of the fine-tuned and the base model in the same session (`results/FINETUNE_REPORT.md`). The LoRA adapter (106 MB) and the zip that contains it are kept locally, not in git. The training data itself stayed on Kaggle: it holds document excerpts |

One change from the download: `20261007-v3-benchmark/fxassist_results/samples.jsonl` (5 MB, vLLM `/metrics` and `nvidia-smi` sampled every 10 s) is stored gzip-compressed as `samples.jsonl.gz` (132 KB) to stay under the repository's 500 KB file limit. `gunzip -k` restores it byte for byte (checked with `cmp`); the zip still holds the original.

Reports built from these files: `results/BENCHMARKS.md` (`make report RUN=results/raw/20261007-v3-benchmark/fxassist_results`), `results/EVAL_REPORT.md`, `results/tensor_parallel.md`.
