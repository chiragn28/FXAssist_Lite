# Kaggle GPU lab playbook

How to run `notebooks/fxassist_gpu_lab.ipynb` on a free Kaggle T4 (or Colab), what was verified about the free tiers, the hour budget, and what to do when a session dies. Phases 6 and 7 run from this. ADR-004, ADR-005, ADR-006.

## 1. Facts: verified, and not

| Fact | Status | Source, date |
|---|---|---|
| vLLM 0.31.0 (2026-10-05) supports NVIDIA compute capability 7.5 and above; the T4 is listed | **Verified** | docs.vllm.ai, GPU installation page, read 2026-10-07 |
| vLLM 0.31.0 wheels: CUDA 12.9 build, Python 3.10-3.13, bundled PyTorch; install in a fresh environment | **Verified** | same page |
| Attention backend on a T4 (vLLM 0.31.0): FlashAttention needs SM 8.0; FlashInfer is **deliberately excluded on SM 7.5** ("currently broken on SM75", flashinfer issue 3620); TRITON_ATTN accepts any compute capability. Expected automatic choice: TRITON_ATTN. The smoke test records the real one | **Verified in source** | `vllm/platforms/cuda.py`, `v1/attention/backends/{flash_attn,flashinfer,triton_attn}.py` of the 0.31.0 wheel, 2026-10-07 |
| Every CLI flag the lab passes (`--dtype --max-model-len --gpu-memory-utilization --max-num-seqs --tensor-parallel-size --enable-prefix-caching/--no-enable-prefix-caching --quantization --attention-backend --served-model-name --seed`) exists | **Verified** | `vllm serve --help=all` of the installed 0.31.0 |
| `--dtype auto` uses BF16 for BF16 models; Qwen2.5-3B-Instruct's `config.json` says `bfloat16`; the T4 has no BF16, so the lab passes `--dtype float16` (GPU-02) | **Verified** | vLLM help text; huggingface.co config.json, 2026-10-06 |
| AWQ and AWQ-Marlin are supported on Turing (SM 7.5) | **Verified** | docs.vllm.ai quantization hardware table, 2026-10-07 |
| `ignore_eos` is accepted by the chat completions API (fixed output length for benchmarks) | **Verified in source** | `entrypoints/openai/chat_completion/protocol.py`; the smoke test checks it on the GPU |
| Qwen/Qwen2.5-3B-Instruct: not gated, 6.17 GB safetensors, Qwen Research License (non-commercial) | **Verified** | huggingface.co API and LICENSE, 2026-10-06 |
| Qwen/Qwen2.5-3B-Instruct-AWQ: not gated, 2.69 GB, 4-bit AWQ GEMM, group size 128, Qwen Research License | **Verified** | huggingface.co API, config.json |
| Qwen/Qwen2.5-7B-Instruct-AWQ (stretch): 5.57 GB, Apache-2.0 | **Verified** | huggingface.co API |
| Kaggle weekly GPU quota "about 30 hours", sometimes more | **Unverified** | Only secondary sources (Kaggle's own pages render client-side and could not be read). Check *your* number at kaggle.com > Settings, or the quota bar in the notebook editor, and write it here |
| Kaggle GPU choices: P100 (16 GB) or 2x T4 (2 x 15 GB) | **Unverified** | secondary sources; check the Accelerator menu |
| Kaggle session limit 12 hours; idle sessions stop sooner | **Unverified** | secondary sources. The lab is resumable regardless |
| Internet in a Kaggle notebook needs a phone-verified account | **Unverified** | secondary sources; the environment cell detects missing internet and says what to do (GPU-09) |
| Kaggle disk: `/kaggle/working` (output, kept and downloadable, small quota) vs `/kaggle/tmp` (large, discarded) | **Unverified** | common knowledge, not re-checked: the lab writes only small result files to `/kaggle/working` and puts weights and virtualenvs in `/kaggle/tmp`; the environment cell measures free space (GPU-10) |
| Colab free tier: T4 availability varies, sessions disconnect when idle | **Unverified** | the notebook detects Colab and uses `/content` paths and `google.colab.userdata` secrets (GPU-12) |

When you run the notebook, its first stage writes `environment.json` with what you actually got (GPU names and count, compute capability, free disk, internet). Copy those facts into this table with the date.

## 2. Before the first session (once)

1. Push this repo to a **public** GitHub repository (the notebook clones it). Or zip it and upload it as a Kaggle Dataset named `fxassist-lite`; the notebook uses an attached dataset first.
2. Kaggle account: verify your phone number (needed for internet in notebooks).
3. New notebook > File > Import notebook > `notebooks/fxassist_gpu_lab.ipynb`.
4. Settings: Accelerator **GPU T4 x2** (or a single T4; the tensor-parallel run is then skipped and recorded, GPU-07), Internet **on**, Persistence off.
5. Optional: Add-ons > Secrets > `HF_TOKEN` with a read token. Never paste a token into a cell (GPU-11).
6. Set `REPO_URL` in the parameters cell.
7. On your laptop, run `make lab-dry-run` first: the same code against the mock LLM. If it fails here, it would fail on the GPU.

### Alternative: run it through the Kaggle API (no browser)

Steps 3 to 6 can be replaced by three make targets. The run is a background notebook version, so no browser tab has to stay open.

1. Phone-verify the Kaggle account (still required; only you can do this).
2. Once, in the Ubuntu terminal: `uvx --from kaggle==2.2.4 kaggle auth login` (signs in through your browser; credentials are cached by the CLI and never pass through the repo).
3. Add your Kaggle username to `.env`: `FXA_KAGGLE_USERNAME=<username>`.
4. `make lab-push` builds `.kaggle-kernel/` (the notebook with `REPO_URL` set from the `github` remote, plus `kernel-metadata.json` with `enable_gpu`, `enable_internet` and `machine_shape: NvidiaTeslaT4` = GPU T4 x2, private) and starts the run.
5. `make lab-status` until it says complete, then `make lab-pull` downloads the output to `results/raw/<date>/`.

Field names (`enable_gpu`, `enable_internet`, `machine_shape`) were read from the Kaggle CLI's `docs/kernels_metadata.md` on 2026-10-07. A background version starts with an empty `/kaggle/working`, so finished stages from an earlier version are not skipped; one run fits the budget.

## 3. Session plan and hour budget (GPU-06)

Estimates for planning only; real times replace them after the first session.

| Order | Stage | Priority | Estimated time | Cut if short on hours? |
|---|---|---|---|---|
| 1 | Environment checks, vLLM virtualenv, FP16 download | - | 10-15 min | never |
| 2 | Smoke test (GPU-01): load FP16, one request, record the backend | 1 | 5-10 min (10 min cap) | never: everything else depends on it |
| 3 | FP16 baseline: concurrency 1/4/16/32 x short/long, 3 repetitions | 2 | 40-60 min | no |
| 4 | AWQ baseline, same cells | 3 | 40-60 min | no |
| 5 | Evaluation FP16, then AWQ (ingest + 39 questions + attacks + scenarios) | 4 | 20-30 min each | AWQ eval first to go |
| 6 | Four knobs, one at a time, at concurrency 16 | 5 | 10-15 min each | yes, from the bottom of the list |
| 7 | OOM drill | 6 | 5 min | yes |
| 8 | Tensor parallel, 2 x T4 (Phase 7) | 7 | 30-40 min | first to go |

Total about 4 to 5 hours. `HOUR_BUDGET` (default 4) makes the lab stop *starting* new experiments when less than 15 minutes remain; `MAX_PRIORITY` cuts whole groups. Plan for two sessions in one week rather than one long one.

## 4. When the session dies (GPU-05)

Nothing is lost that was finished:
- every benchmark repetition is a line in `fxassist_results/benchmark.jsonl`, written and `fsync`ed as it ends
- every stage outcome is a line in `stages.jsonl`
- downloads resume partial files

Start a new session with the same settings and **run all cells again**. Finished repetitions are skipped (a half-written last line is ignored and redone). Kaggle may not keep `/kaggle/working` across sessions unless you save a version: download the zip at the end of every session (last cell), or click "Save version" with "Save output".

## 5. GPU memory: the formula (GPU-03)

vLLM takes `gpu_memory_utilization x total memory` and splits it:

```
budget       = gpu_memory_utilization x GPU memory          (0.90 x ~15 GiB = ~13.5 GiB on a T4)
weights      = FP16: 6.17 GB (5.75 GiB)   AWQ: 2.69 GB (2.5 GiB)
overhead     = activations for the largest batch + CUDA graphs + PyTorch reserve (about 1 GiB; measured in Phase 6)
KV cache     = budget - weights - overhead
KV per token = 2 (K and V) x layers x KV heads x head size x bytes
             = 2 x 36 x 2 x 128 x 2 bytes = 36,864 bytes = 36 KiB  (Qwen2.5-3B config.json, FP16 cache)
tokens       = KV cache / 36 KiB
```

Estimate for the FP16 model: (13.5 - 5.75 - ~1) GiB = ~6.7 GiB of KV cache = ~195,000 tokens = ~47 sequences of 4,096 tokens at once. AWQ frees about 3.2 GiB more, roughly 90,000 more tokens. These are estimates; vLLM logs the real values at startup (`Available KV cache memory`, `KV cache size: N tokens`, `Maximum concurrency for 4096 tokens per request: Nx`), and `make report` puts them in a table.

| Knob | What it trades | Out of memory when |
|---|---|---|
| `gpu_memory_utilization` | KV cache size against headroom for other processes and fragmentation | too high (0.99): CUDA out of memory during profiling or the first large batch |
| `max_model_len` | longest prompt + answer against how many fit at once | larger than the KV cache can hold for even one sequence: vLLM refuses to start and says so |
| `max_num_seqs` | concurrency against per-step activation memory and queueing | very high with long prompts: preemptions first (`vllm:num_preemptions_total`), OOM later |

The OOM drill (`gpu_memory_utilization 0.99`, `max_model_len 32768`, `max_num_seqs 256`) records what each failure looks like; Phase 6 turns it into `docs/runbooks/gpu-oom.md`.

## 6. After the session

```bash
mkdir -p results/raw/$(date +%Y%m%d)
unzip ~/Downloads/fxassist_results.zip -d results/raw/$(date +%Y%m%d)
make report RUN=results/raw/$(date +%Y%m%d)/fxassist_results   # writes results/BENCHMARKS.md
```

Review the files before committing (no tokens in logs), then commit `results/`.
