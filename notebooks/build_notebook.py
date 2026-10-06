#!/usr/bin/env python3
"""Build notebooks/fxassist_gpu_lab.ipynb (run `make notebook` after editing).

The notebook is thin on purpose: parameters, then one call per stage into bench/lab.py, which is
tested in mock mode (bench/tests). Edit this file, not the .ipynb.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fxassist_gpu_lab.ipynb"

CELLS: list[tuple[str, str]] = [
    (
        "markdown",
        """# FXAssist Lite: GPU lab (Kaggle T4, or Colab)

Serves **Qwen2.5-3B-Instruct** (FP16 and AWQ) with **vLLM** on a free T4, runs the benchmark matrix and the evaluation set, and packs every result file into one zip to download. Plan and hour budget: `docs/KAGGLE_PLAYBOOK.md`. Method: `docs/BENCHMARK_METHOD.md`.

**Before running:** Settings > Accelerator > **GPU T4 x2** (or T4); Settings > **Internet on** (needs a phone-verified account). Optional: Add-ons > Secrets > `HF_TOKEN` (the models are not gated; a token only avoids download rate limits).

Every stage appends to `fxassist_results/stages.jsonl` as it ends, and benchmark rows are written one by one, so if the session dies, **run all cells again: finished work is skipped** (GPU-05).

Licences: Qwen2.5-3B-Instruct(-AWQ) are under the *Qwen Research License* (non-commercial research use); the optional 7B AWQ model is Apache-2.0.""",
    ),
    (
        "code",
        """# Parameters
import os
from pathlib import Path

REPO_URL = "https://github.com/<you>/fxassist_lite"  # your public copy of the repo
HOUR_BUDGET = 4.0     # stop starting new experiments after this many hours (GPU-06)
MAX_PRIORITY = 7      # 2 fp16, 3 awq, 5 knobs, 6 OOM drill, 7 tensor parallel; lower to cut work
RUN_EVAL = True       # answer quality with the real model (about 30 minutes per variant)
RUN_OOM_DRILL = True  # deliberately run out of GPU memory, last (GPU-03)

MODE = "kaggle" if Path("/kaggle").exists() else "colab" if Path("/content").exists() else "mock"
print("mode:", MODE)""",
    ),
    (
        "code",
        """# Get the code: a Kaggle dataset named fxassist-lite if attached, otherwise git clone.
import subprocess, sys
scratch = Path("/kaggle/tmp" if MODE == "kaggle" else "/content/scratch" if MODE == "colab" else ".lab-scratch")
scratch.mkdir(parents=True, exist_ok=True)
dataset = Path("/kaggle/input/fxassist-lite")
REPO_DIR = dataset if dataset.exists() else scratch / "fxassist_lite"
if not REPO_DIR.exists():
    subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(REPO_DIR)], check=True)
sys.path.insert(0, str(REPO_DIR))
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "httpx", "pyyaml"], check=True)
print("code at", REPO_DIR)""",
    ),
    (
        "code",
        """# Hugging Face token from notebook secrets, if set (GPU-11). Never printed, never written to a file.
try:
    if MODE == "kaggle":
        from kaggle_secrets import UserSecretsClient
        os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN")
    elif MODE == "colab":
        from google.colab import userdata
        os.environ["HF_TOKEN"] = userdata.get("HF_TOKEN")
    print("HF_TOKEN: set")
except Exception:
    print("HF_TOKEN: not set (fine: the models are not gated)")""",
    ),
    ("markdown", "## 1. Environment checks (GPU count, compute capability, internet, disk)"),
    (
        "code",
        """from bench import lab
cfg = lab.LabConfig.for_mode(MODE, hour_budget=HOUR_BUDGET, max_priority=MAX_PRIORITY)
env = lab.check_environment(cfg)
print({k: env[k] for k in ("gpus", "bf16_supported", "internet", "scratch_free_gb")})
if env["problems"]:
    raise SystemExit("Fix first:\\n- " + "\\n- ".join(env["problems"]))""",
    ),
    (
        "markdown",
        "## 2. vLLM in its own virtualenv (GPU-04), then the 10-minute smoke test (GPU-01)",
    ),
    (
        "code",
        """lab.install_vllm(cfg)
lab.download_model(cfg, "Qwen/Qwen2.5-3B-Instruct")
smoke = lab.smoke_test(cfg)
smoke""",
    ),
    ("markdown", "## 3. Benchmarks, in priority order, within the hour budget"),
    (
        "code",
        """lab.run_experiments(cfg, smoke=smoke)
print(f"{cfg.hours_left:.2f} budget hours left")""",
    ),
    ("markdown", "## 4. Evaluation with the real model (ADR-016)"),
    (
        "code",
        """if RUN_EVAL and cfg.hours_left > 0.5:
    for variant in ("fp16", "awq"):
        print(lab.run_eval(cfg, variant, REPO_DIR))""",
    ),
    (
        "markdown",
        "## 5. Out-of-memory drill (GPU-03): settings chosen to fail; the error text is the result",
    ),
    (
        "code",
        """if RUN_OOM_DRILL:
    lab.oom_drill(cfg)""",
    ),
    ("markdown", "## 6. Package and download"),
    (
        "code",
        """zip_path = lab.package(cfg)
print("Download:", zip_path)
if MODE == "colab":
    from google.colab import files
    files.download(str(zip_path))
# Kaggle: the zip is in the Output panel (/kaggle/working). Before sharing this notebook,
# check that no output cell shows a token (GPU-11).""",
    ),
    (
        "markdown",
        """## Next, on your laptop

```bash
mkdir -p results/raw/$(date +%Y%m%d) && unzip fxassist_results.zip -d results/raw/$(date +%Y%m%d)
make report RUN=results/raw/$(date +%Y%m%d)/fxassist_results
```""",
    ),
]


def build() -> dict:
    cells = []
    for kind, source in CELLS:
        cell = {"cell_type": kind, "metadata": {}, "source": source.splitlines(keepends=True)}
        if kind == "code":
            cell |= {"execution_count": None, "outputs": []}
        cells.append(cell)
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "accelerator": "GPU",
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    OUT.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
