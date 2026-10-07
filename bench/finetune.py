"""The fine-tuning run in the GPU lab (ADR-026), called from notebooks/fxassist_finetune.ipynb.

    teacher (Qwen2.5-7B-Instruct-AWQ, Apache-2.0) + the real agent  ->  validated examples
    LoRA on Qwen2.5-3B-Instruct (FP16), time-limited                ->  merged FP16 model
    the usual evaluation on the fine-tuned model, then on the base  ->  a fair comparison

Every stage is recorded in stages.jsonl (GPU-05). Training data stays in scratch: it contains
document excerpts, and not every source may be redistributed; only reports and logs are kept.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .lab import (
    LOCAL_MODEL_PREFIX,
    LabConfig,
    Server,
    download_model,
    make_venv,
    record,
    run,
    run_eval,
)
from .matrix import Experiment

PEFT_PACKAGES = ["peft==0.21.2", "accelerate==1.15.0"]  # verified on PyPI 2026-10-07
STUDENT_VARIANT = "fp16"
TEACHER_VARIANT = "awq-7b"
FINETUNED_MODEL = f"{LOCAL_MODEL_PREFIX}qwen2.5-3b-fxassist-lora"


def model_dir(cfg: LabConfig, repo: str) -> Path:
    """Where Server expects a model's files."""
    return cfg.scratch_dir / "models" / repo.replace("/", "--")


def finetuned_config() -> dict[str, Any]:
    return Experiment("baseline", STUDENT_VARIANT, 0).server_config | {
        "model": FINETUNED_MODEL,
        "quantization": None,
    }


def agent_env(cfg: LabConfig, server: Server, config: dict[str, Any], eval_dir: Path) -> dict:
    return dict(os.environ) | {
        "FXA_QDRANT_PATH": str(cfg.scratch_dir / "qdrant"),
        "FXA_DATA_DIR": str(cfg.scratch_dir / "data"),
        "FXA_EVAL_DIR": str(eval_dir),
        "FXA_LLM_BASE_URL": server.base_url,
        "FXA_LLM_MODEL": server.served_model,
        "FXA_LLM_MAX_CONTEXT_TOKENS": str(config["max_model_len"]),
        "FXA_MAX_REQUEST_SECONDS": "600",  # many concurrent runs share one teacher
        "PYTHONPATH": str(cfg.scratch_dir / "repo-bench"),
    }


def make_data(cfg: LabConfig, repo_dir: Path, chunks: int) -> dict[str, Any]:
    out = cfg.scratch_dir / "ft-data"
    if (out / "train.jsonl").exists():
        return record(cfg, "ft-data", "skipped", note="already built")
    python = make_venv(cfg, "eval-venv", ["-e", str(repo_dir / "services" / "agent")])
    fxassist = str(python.parent / "fxassist")
    config = Experiment("baseline", TEACHER_VARIANT, 0).server_config
    download_model(cfg, config["model"])
    # `bench` must be importable from the eval venv without the rest of the repo on its path
    bench_root = cfg.scratch_dir / "repo-bench"
    shutil.copytree(repo_dir / "bench", bench_root / "bench", dirs_exist_ok=True)
    eval_dir = cfg.scratch_dir / "ft-eval-questions"
    shutil.copytree(
        repo_dir / "eval", eval_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("runs")
    )
    (cfg.scratch_dir / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(repo_dir / "data" / "sources.yaml", cfg.scratch_dir / "data" / "sources.yaml")

    server = Server(cfg, config, "ft-teacher")
    server.start()
    ok, why = server.wait_ready(cfg.server_start_timeout_s)
    if not ok:
        server.stop()
        return record(cfg, "ft-data", "server-failed", note=why[-500:])
    env = agent_env(cfg, server, config, eval_dir)
    try:
        ingest = run([fxassist, "ingest"], timeout=3600, env=env, cwd=repo_dir)
        if ingest.returncode != 0:
            return record(cfg, "ft-data", "ingest-failed", note=ingest.stderr[-1000:])
        gen = run(
            [str(python), "-m", "bench.ft_data", "--out", str(out), "--chunks", str(chunks)],
            timeout=5400,
            env=env,
            cwd=bench_root,
        )
    finally:
        server.stop()
    target = cfg.results_dir / "finetune"
    target.mkdir(parents=True, exist_ok=True)
    (target / "data-stdout.txt").write_text(gen.stdout[-20000:] + "\n" + gen.stderr[-20000:])
    for name in ("data-report.json", "agent-runs.jsonl"):
        if (out / name).exists():
            shutil.copy(out / name, target / name)
    if gen.returncode != 0:
        return record(cfg, "ft-data", "failed", note=gen.stderr[-1000:])
    return record(cfg, "ft-data", "ok", report=json.loads((out / "data-report.json").read_text()))


def train(cfg: LabConfig, repo_dir: Path, max_minutes: float) -> dict[str, Any]:
    merged = model_dir(cfg, FINETUNED_MODEL)
    if (merged / "config.json").exists():
        return record(cfg, "ft-train", "skipped", note="merged model already exists")
    data = cfg.scratch_dir / "ft-data"
    if not (data / "train.jsonl").exists():
        return record(cfg, "ft-train", "skipped", note="no training data")
    python = make_venv(cfg, "vllm-venv", [f"vllm=={cfg.vllm_version}", *PEFT_PACKAGES])
    base = download_model(cfg, Experiment("baseline", STUDENT_VARIANT, 0).server_config["model"])
    log = cfg.results_dir / "finetune" / "train-log.jsonl"
    out = run(
        [
            str(python),
            "-m",
            "bench.ft_train",
            "--base",
            str(base),
            "--data",
            str(data),
            "--adapter",
            str(cfg.scratch_dir / "ft-adapter"),
            "--merged",
            str(merged),
            "--log",
            str(log),
            "--max-minutes",
            str(max_minutes),
        ],
        timeout=max_minutes * 60 + 1800,  # plus loading, validation and the merge
        cwd=repo_dir,
        env=dict(os.environ) | {"CUDA_VISIBLE_DEVICES": "0"},  # one T4 is enough for LoRA
    )
    (cfg.results_dir / "finetune" / "train-stdout.txt").write_text(
        out.stdout[-20000:] + "\n" + out.stderr[-20000:]
    )
    if out.returncode != 0:
        return record(cfg, "ft-train", "failed", note=out.stderr[-1500:])
    # The adapter is small (tens of MB): keep it with the results, compressed.
    adapter = cfg.scratch_dir / "ft-adapter"
    archive = shutil.make_archive(
        str(cfg.results_dir / "finetune" / "lora-adapter"), "gztar", adapter
    )
    return record(cfg, "ft-train", "ok", adapter=archive)


def evaluate(cfg: LabConfig, repo_dir: Path) -> list[dict[str, Any]]:
    results = []
    if (model_dir(cfg, FINETUNED_MODEL) / "config.json").exists():
        results.append(run_eval(cfg, "fp16-lora", repo_dir, config=finetuned_config()))
    else:
        results.append(record(cfg, "eval fp16-lora", "skipped", note="no fine-tuned model"))
    if cfg.hours_left > 0.3:  # the same evaluation on the base model, in the same session
        results.append(run_eval(cfg, STUDENT_VARIANT, repo_dir))
    else:
        results.append(record(cfg, "eval fp16", "skipped", note="hour budget"))
    return results
