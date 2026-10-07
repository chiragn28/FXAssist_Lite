"""The GPU lab, as functions the notebook calls (ADR-004, Phase 5).

The notebook (notebooks/fxassist_gpu_lab.ipynb) only sets parameters and calls these. Keeping the
logic here means it is linted, tested, and runs the same way in three modes:
  kaggle  free T4 GPU(s) in a Kaggle notebook
  colab   free T4 in Google Colab (GPU-12): other paths and secrets API, same steps
  mock    no GPU: the mock LLM stands in for vLLM, so the whole flow can be dry-run locally
          and in CI before spending GPU hours (Phase 5 acceptance)

Every stage writes its outcome to `<results>/stages.jsonl` as it finishes, so a session that is
killed still leaves everything done so far on disk (GPU-05).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from .harness import ResultLog, run_cell
from .matrix import EXPERIMENTS, OOM_DRILL, Experiment, check_one_variable
from .prompts import prompt_sets

Mode = Literal["kaggle", "colab", "mock"]
ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """A port the OS reports as free right now (mock mode only; Kaggle and Colab use 8000)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LabConfig:
    mode: Mode
    results_dir: Path  # small files only: this is what gets downloaded
    scratch_dir: Path  # virtualenvs and model weights: large, never downloaded
    vllm_version: str = "0.31.0"
    port: int = 8000
    hour_budget: float = 4.0  # GPU-06: stop starting new work after this
    max_priority: int = 7  # run experiments up to this priority (see bench/matrix.py)
    gpu_count: int = 0  # set by check_environment
    server_start_timeout_s: float = 900.0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def hours_left(self) -> float:
        return self.hour_budget - (time.monotonic() - self.started_at) / 3600

    @classmethod
    def for_mode(cls, mode: Mode, **overrides: Any) -> LabConfig:
        if mode == "kaggle":  # /kaggle/working is the downloadable output (small quota)
            paths = {
                "results_dir": Path("/kaggle/working/fxassist_results"),
                "scratch_dir": Path("/kaggle/tmp"),
            }
        elif mode == "colab":
            paths = {
                "results_dir": Path("/content/fxassist_results"),
                "scratch_dir": Path("/content/scratch"),
            }
        else:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            paths = {
                "results_dir": ROOT / "results" / "mock" / stamp,
                "scratch_dir": ROOT / ".lab-scratch",
                # Locally 8000 may already be taken (the compose gateway uses it): CI-01.
                "port": _free_port(),
            }
        return cls(mode=mode, **(paths | overrides))


def record(cfg: LabConfig, stage: str, status: str, **details: Any) -> dict[str, Any]:
    """Append one stage outcome to stages.jsonl immediately (GPU-05)."""
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "stage": stage,
        "status": status,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **details,
    }
    with (cfg.results_dir / "stages.jsonl").open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    print(
        f"[{row['at']}] {stage}: {status}"
        + (f" ({details.get('note')})" if details.get("note") else "")
    )
    return row


def run(cmd: list[str], timeout: float = 120, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False, **kwargs
    )


# --- 1. Environment checks (GPU-02, GPU-07, GPU-09, GPU-10) -------------------------------------


def gpus() -> list[dict[str, Any]]:
    if shutil.which("nvidia-smi") is None:
        return []
    out = run(
        ["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits"]
    )
    found = []
    for line in out.stdout.strip().splitlines():
        name, mem, cap = (x.strip() for x in line.split(","))
        found.append(
            {"name": name, "memory_mib": int(float(mem)), "compute_capability": float(cap)}
        )
    return found


def internet_ok(url: str = "https://huggingface.co", timeout: float = 8) -> bool:
    try:
        return httpx.head(url, timeout=timeout, follow_redirects=True).status_code < 500
    except httpx.HTTPError:
        return False


def check_environment(cfg: LabConfig) -> dict[str, Any]:
    cfg.scratch_dir.mkdir(parents=True, exist_ok=True)
    found = gpus()
    disk = shutil.disk_usage(cfg.scratch_dir)
    info: dict[str, Any] = {
        "mode": cfg.mode,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gpus": found,
        "gpu_count": len(found),
        # GPU-02: bfloat16 needs compute capability 8.0+; a T4 is 7.5, so float16 only.
        "bf16_supported": bool(found) and all(g["compute_capability"] >= 8.0 for g in found),
        "internet": internet_ok(),
        "scratch_free_gb": round(disk.free / 1e9, 1),
        "cpu_count": os.cpu_count(),
    }
    problems = []
    if cfg.mode != "mock":
        if not found:
            problems.append(
                "No GPU. Kaggle: Settings > Accelerator > GPU T4 x2 (or T4). Colab: Runtime > Change runtime type > T4 GPU."
            )
        if not info["internet"]:  # GPU-09
            problems.append(
                "No internet. Kaggle: Settings > Internet on (the switch needs a phone-verified "
                "account: kaggle.com/settings > Phone verification). Then restart the session."
            )
        needed_gb = 6.2 + 2.7 + 8  # fp16 + awq weights + vLLM and eval virtualenvs (GPU-10)
        if info["scratch_free_gb"] < needed_gb:
            problems.append(
                f"Only {info['scratch_free_gb']} GB free in {cfg.scratch_dir}; need about {needed_gb:.0f} GB."
            )
    info["problems"] = problems
    cfg.gpu_count = len(found)
    (cfg.results_dir / "environment.json").parent.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "environment.json").write_text(json.dumps(info, indent=2))
    record(
        cfg,
        "environment",
        "ok" if not problems else "blocked",
        gpu_count=len(found),
        note="; ".join(problems) or f"{len(found)} GPU(s), bf16={info['bf16_supported']}",
    )
    if len(found) < 2:  # GPU-07
        record(
            cfg,
            "multi-gpu",
            "skipped",
            note=f"{len(found)} GPU(s) assigned; tensor-parallel run needs 2",
        )
    return info


# --- 2. Isolated virtualenvs (GPU-04) ----------------------------------------------------------


def venv_python(cfg: LabConfig, name: str) -> Path:
    return cfg.scratch_dir / name / "bin" / "python"


def make_venv(cfg: LabConfig, name: str, packages: list[str], timeout: float = 1800) -> Path:
    """A fresh virtualenv, so the notebook's preinstalled packages cannot clash (GPU-04)."""
    python = venv_python(cfg, name)
    if not python.exists():
        out = run([sys.executable, "-m", "venv", str(cfg.scratch_dir / name)])
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
    run([str(python), "-m", "pip", "install", "-q", "--upgrade", "pip", "uv"], timeout=600)
    out = run(
        [str(python), "-m", "uv", "pip", "install", "--python", str(python), *packages],
        timeout=timeout,
    )
    if out.returncode != 0:
        raise RuntimeError(f"installing {packages} failed:\n{out.stderr[-2000:]}")
    return python


def install_vllm(cfg: LabConfig) -> dict[str, Any]:
    if cfg.mode == "mock":
        return record(cfg, "install-vllm", "skipped", note="mock mode")
    python = make_venv(cfg, "vllm-venv", [f"vllm=={cfg.vllm_version}"])
    version = run(
        [
            str(python),
            "-c",
            "import vllm, torch; print(vllm.__version__, torch.__version__, torch.version.cuda)",
        ]
    )
    return record(cfg, "install-vllm", "ok", versions=version.stdout.strip())


# --- 3. Model download with retries and resume (DEP-06, GPU-10, GPU-11) -------------------------

NEEDED_FILES = ["*.json", "*.safetensors", "*.txt", "tokenizer*", "merges.txt", "vocab.json"]


def download_model(cfg: LabConfig, repo: str, attempts: int = 5) -> Path:
    """snapshot_download resumes partial files; retry with backoff on network errors. Only
    the files vLLM needs are fetched (no .bin/.pth duplicates, GPU-10). The Hugging Face token,
    if any, comes from the environment (set from notebook secrets) and is never printed."""
    target = cfg.scratch_dir / "models" / repo.replace("/", "--")
    if cfg.mode == "mock":
        return target
    python = venv_python(cfg, "vllm-venv")
    code = (
        "import sys; from huggingface_hub import snapshot_download; "
        f"snapshot_download({repo!r}, local_dir={str(target)!r}, allow_patterns={NEEDED_FILES!r}, max_workers=4)"
    )
    for attempt in range(1, attempts + 1):
        out = run([str(python), "-c", code], timeout=3600)
        if out.returncode == 0:
            record(cfg, f"download {repo}", "ok", attempt=attempt)
            return target
        record(
            cfg,
            f"download {repo}",
            "retry",
            attempt=attempt,
            error=out.stderr.strip().splitlines()[-1:],
        )
        time.sleep(min(60, 5 * 2**attempt))
    raise RuntimeError(
        f"Could not download {repo} after {attempts} attempts. Check that Internet is on, "
        f"there is free disk in {cfg.scratch_dir}, and huggingface.co is reachable. Partial "
        "files are kept: running this cell again resumes them."
    )


# --- 4. The model server ----------------------------------------------------------------------


@dataclass
class Server:
    cfg: LabConfig
    config: dict[str, Any]
    name: str
    process: subprocess.Popen | None = None
    log_path: Path | None = None
    served_model: str = "fxassist-model"
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.port}/v1"

    def command(self) -> list[str]:
        if self.cfg.mode == "mock":
            return [sys.executable, "-m", "fxassist_mock_llm.app"]
        c = self.config
        model_path = self.cfg.scratch_dir / "models" / c["model"].replace("/", "--")
        cmd = [
            str(venv_python(self.cfg, "vllm-venv").parent / "vllm"),
            "serve",
            str(model_path),
            "--served-model-name",
            self.served_model,
            "--port",
            str(self.cfg.port),
            "--host",
            "127.0.0.1",
            "--dtype",
            c["dtype"],  # GPU-02: float16 explicitly, never "auto" on a T4
            "--max-model-len",
            str(c["max_model_len"]),
            "--gpu-memory-utilization",
            str(c["gpu_memory_utilization"]),
            "--max-num-seqs",
            str(c["max_num_seqs"]),
            "--tensor-parallel-size",
            str(c["tensor_parallel_size"]),
            "--seed",
            "0",
            "--enable-prefix-caching"
            if c["enable_prefix_caching"]
            else "--no-enable-prefix-caching",
        ]
        if c.get("quantization"):
            cmd += ["--quantization", c["quantization"]]
        if c.get("attention_backend"):
            cmd += ["--attention-backend", c["attention_backend"]]
        return cmd

    def start(self) -> None:
        self.log_path = self.cfg.results_dir / "server-logs" / f"{self.name}.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ) | self.extra_env
        if self.cfg.mode == "mock":
            env |= {
                "FXA_MOCK_PORT": str(self.cfg.port),
                "FXA_MOCK_HOST": "127.0.0.1",
                "FXA_MOCK_MODELS": self.served_model,
                "FXA_MOCK_CONFIG": json.dumps(
                    {
                        "flavour": "vllm",
                        "first_token_ms": 20,
                        "token_ms": 2,
                        "reply": " ".join(["token"] * 300),
                    }
                ),
            }
        log = self.log_path.open("w")
        self.process = subprocess.Popen(
            self.command(), stdout=log, stderr=subprocess.STDOUT, env=env
        )

    def wait_ready(self, timeout_s: float) -> tuple[bool, str]:
        """Ready when /v1/models answers; fails early if the process dies (e.g. out of memory)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                return (
                    False,
                    f"server exited with code {self.process.returncode}: {self.log_tail()}",
                )
            try:
                if httpx.get(f"{self.base_url}/models", timeout=3).status_code == 200:
                    return True, "ready"
            except httpx.HTTPError:
                pass
            time.sleep(2)
        return False, f"not ready after {timeout_s:.0f}s: {self.log_tail()}"

    def log_tail(self, lines: int = 15) -> str:
        if not self.log_path or not self.log_path.exists():
            return ""
        return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(30)
        self.process = None
        time.sleep(3 if self.cfg.mode != "mock" else 0)  # let the GPU memory be released


# --- 5. Samplers: vLLM /metrics and GPU memory, to files (ADR-012) ----------------------------


class Sampler(threading.Thread):
    def __init__(self, cfg: LabConfig, server: Server, every_s: float = 10.0):
        super().__init__(daemon=True)
        self.cfg, self.server, self.every_s = cfg, server, every_s
        self.stopped = threading.Event()

    def run(self) -> None:
        path = self.cfg.results_dir / "samples.jsonl"
        root = self.server.base_url.removesuffix("/v1")
        while not self.stopped.wait(self.every_s):
            row: dict[str, Any] = {"t": time.time(), "server": self.server.name}
            try:
                text = httpx.get(f"{root}/metrics", timeout=3).text
                row["vllm"] = {
                    line.split(" ")[0]: line.rsplit(" ", 1)[-1]
                    for line in text.splitlines()
                    if line.startswith("vllm:") and ("_count" in line or "_bucket" not in line)
                }
            except httpx.HTTPError:
                row["vllm"] = None
            if shutil.which("nvidia-smi"):
                out = run(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,memory.used,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ]
                )
                row["gpu"] = [line.split(", ") for line in out.stdout.strip().splitlines()]
            with path.open("a") as f:
                f.write(json.dumps(row) + "\n")

    def stop(self) -> None:
        self.stopped.set()


# --- 6. Smoke test (GPU-01) --------------------------------------------------------------------


def smoke_test(cfg: LabConfig) -> dict[str, Any]:
    """Ten minutes, before anything else: does this vLLM version run this model on this GPU?

    Tries vLLM's default attention backend first, then TRITON_ATTN (listed for any compute
    capability in the vLLM docs). Records which one worked, and checks `ignore_eos` gives an
    exact output length, which the benchmark relies on (BEN-08).
    """
    baseline = next(e for e in EXPERIMENTS if e.name == "baseline" and e.variant == "fp16")
    attempts = [None] if cfg.mode == "mock" else [None, "TRITON_ATTN"]
    for backend in attempts:
        config = baseline.server_config | ({"attention_backend": backend} if backend else {})
        server = Server(cfg, config, f"smoke-{backend or 'default'}")
        server.start()
        ok, why = server.wait_ready(min(cfg.server_start_timeout_s, 600))
        if not ok:
            server.stop()
            record(cfg, "smoke", "failed", backend=backend or "default", note=why[-500:])
            continue
        r = httpx.post(
            f"{server.base_url}/chat/completions",
            timeout=120,
            json={
                "model": server.served_model,
                "max_tokens": 16,
                "temperature": 0,
                "ignore_eos": True,
                "messages": [{"role": "user", "content": "What is a pip in forex?"}],
            },
        ).json()
        tokens = r.get("usage", {}).get("completion_tokens")
        log = server.log_tail(400)
        server.stop()
        chosen = re.findall(r"[Uu]sing (\w+) (?:attention )?backend", log)
        result = record(
            cfg,
            "smoke",
            "ok",
            backend=backend or "default",
            backend_seen_in_log=chosen[-1:] or None,
            completion_tokens=tokens,
            ignore_eos_exact=tokens == 16,
            sample=(r.get("choices") or [{}])[0].get("message", {}).get("content", "")[:200],
        )
        (cfg.results_dir / "smoke.json").write_text(json.dumps(result, indent=2))
        return result
    raise RuntimeError(
        "Smoke test failed with every attention backend; see stages.jsonl and server-logs/."
    )


# --- 7. Experiments in priority order, within the hour budget (GPU-05, GPU-06) ------------------


def run_experiments(
    cfg: LabConfig, experiments: list[Experiment] | None = None, smoke: dict[str, Any] | None = None
) -> None:
    experiments = experiments if experiments is not None else EXPERIMENTS
    check_one_variable(experiments)  # BEN-07
    sets = prompt_sets()
    log = ResultLog(cfg.results_dir / "benchmark.jsonl")
    backend = smoke.get("backend") if smoke else None
    for e in sorted(experiments, key=lambda e: e.priority):
        if e.priority > cfg.max_priority:
            record(
                cfg, f"experiment {e.name}/{e.variant}", "skipped", note="below the priority cut"
            )
            continue
        if cfg.hours_left < 0.25:  # GPU-06: never start what cannot finish
            record(cfg, f"experiment {e.name}/{e.variant}", "skipped", note="hour budget used up")
            continue
        pending = [
            c for c in e.cells if any(not log.is_done(c, r) for r in range(1, c.repetitions + 1))
        ]
        if not pending:
            record(cfg, f"experiment {e.name}/{e.variant}", "already-done")
            continue
        config = e.server_config | (
            {"attention_backend": backend} if backend and backend != "default" else {}
        )
        tp = config["tensor_parallel_size"]
        if tp > 1 and cfg.mode != "mock" and cfg.gpu_count < tp:  # GPU-07
            record(
                cfg,
                f"experiment {e.name}/{e.variant}",
                "skipped",
                note=f"needs {tp} GPUs, {cfg.gpu_count} assigned",
            )
            continue
        if cfg.mode != "mock":
            download_model(cfg, config["model"])
        server = start_server(cfg, config, f"{e.name}-{e.variant}")
        if server is None:
            continue
        sampler = Sampler(cfg, server)
        sampler.start()
        try:
            for cell in pending:
                rows = run_cell(
                    cell,
                    base_url=server.base_url,
                    model=server.served_model,
                    prompts=sets[cell.prompt_set],
                    server=config,
                    log=log,
                )
                for row in rows:
                    print(
                        f"  {cell.key} rep{row['repetition']}: p50 {row['latency_p50_s']}, "
                        f"errors {row['error_rate']}, {row['throughput_tokens_per_s']} tok/s"
                    )
        finally:
            sampler.stop()
            server.stop()
        record(cfg, f"experiment {e.name}/{e.variant}", "ok", cells=len(pending))


def start_server(cfg: LabConfig, config: dict[str, Any], name: str) -> Server | None:
    """Start and wait. A multi-GPU server that fails gets one retry with the documented NCCL
    workaround for GPUs without peer-to-peer access over PCIe (GPU-08); both errors are kept."""
    attempts: list[dict[str, str]] = [{}]
    if config["tensor_parallel_size"] > 1:
        attempts.append({"NCCL_P2P_DISABLE": "1"})
    for extra_env in attempts:
        server = Server(
            cfg, config, name + ("-nccl-p2p-off" if extra_env else ""), extra_env=extra_env
        )
        server.start()
        ok, why = server.wait_ready(cfg.server_start_timeout_s)
        if ok:
            if extra_env:
                record(cfg, f"server {name}", "ok-with-workaround", env=extra_env)
            return server
        server.stop()
        record(cfg, f"server {name}", "failed", env=extra_env, note=why[-1500:])
    return None


# --- 8. Induced out-of-memory drill (GPU-03) ---------------------------------------------------


def oom_drill(cfg: LabConfig) -> dict[str, Any]:
    if cfg.mode == "mock":
        return record(cfg, "oom-drill", "skipped", note="mock mode")
    server = Server(cfg, OOM_DRILL.server_config, "oom-drill")
    server.start()
    ok, why = server.wait_ready(cfg.server_start_timeout_s)
    gpu_now = run(["nvidia-smi"]).stdout if shutil.which("nvidia-smi") else ""
    server.stop()
    result = record(
        cfg,
        "oom-drill",
        "started-anyway" if ok else "failed-as-expected",
        settings=OOM_DRILL.server,
        error_tail=why[-3000:],
        nvidia_smi=gpu_now[-2000:],
    )
    (cfg.results_dir / "oom-drill.json").write_text(json.dumps(result, indent=2))
    return result


# --- 9. Evaluation with the real model (ADR-016) -----------------------------------------------


def run_eval(cfg: LabConfig, variant: str, repo_dir: Path) -> dict[str, Any]:
    """`fxassist ingest` + `fxassist eval` against vLLM, with Qdrant in embedded mode (ADR-008)."""
    if cfg.mode == "mock":
        return record(
            cfg, f"eval {variant}", "skipped", note="mock mode: needs the real corpus and model"
        )
    python = make_venv(cfg, "eval-venv", ["-e", str(repo_dir / "services" / "agent")])
    config = next(
        e for e in EXPERIMENTS if e.name == "baseline" and e.variant == variant
    ).server_config
    server = Server(cfg, config, f"eval-{variant}")
    download_model(cfg, config["model"])
    server.start()
    ok, why = server.wait_ready(cfg.server_start_timeout_s)
    if not ok:
        server.stop()
        return record(cfg, f"eval {variant}", "server-failed", note=why[-500:])
    # The repo may be a read-only Kaggle dataset: questions are copied, runs written to scratch.
    eval_dir = cfg.scratch_dir / f"eval-{variant}"
    shutil.copytree(
        repo_dir / "eval", eval_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("runs")
    )
    env = dict(os.environ) | {
        "FXA_QDRANT_PATH": str(cfg.scratch_dir / "qdrant"),
        "FXA_DATA_DIR": str(cfg.scratch_dir / "data"),
        "FXA_EVAL_DIR": str(eval_dir),
        "FXA_LLM_BASE_URL": server.base_url,
        "FXA_LLM_MODEL": server.served_model,
        "FXA_LLM_MAX_CONTEXT_TOKENS": str(config["max_model_len"]),
    }
    (cfg.scratch_dir / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(repo_dir / "data" / "sources.yaml", cfg.scratch_dir / "data" / "sources.yaml")
    fxassist = str(python.parent / "fxassist")
    try:
        ingest = run([fxassist, "ingest"], timeout=3600, env=env, cwd=repo_dir)
        if ingest.returncode != 0:
            return record(cfg, f"eval {variant}", "ingest-failed", note=ingest.stderr[-1000:])
        out = run([fxassist, "eval"], timeout=7200, env=env, cwd=repo_dir)
    finally:
        server.stop()
    runs = sorted((eval_dir / "runs").glob("*/"), key=lambda p: p.stat().st_mtime)
    target = cfg.results_dir / "eval" / variant
    if runs:
        shutil.copytree(runs[-1], target, dirs_exist_ok=True)
    (target / "stdout.txt").parent.mkdir(parents=True, exist_ok=True)
    (target / "stdout.txt").write_text(out.stdout[-20000:])
    return record(
        cfg, f"eval {variant}", "ok" if out.returncode == 0 else "failed", run_dir=str(target)
    )


# --- 10. Package for download ------------------------------------------------------------------


def package(cfg: LabConfig) -> Path:
    zip_path = cfg.results_dir.parent / f"{cfg.results_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in cfg.results_dir.rglob("*"):
            if path.is_file():
                z.write(path, path.relative_to(cfg.results_dir.parent))
    record(cfg, "package", "ok", zip=str(zip_path), size_kb=zip_path.stat().st_size // 1024)
    return zip_path


def summary(cfg: LabConfig) -> dict[str, Any]:
    return asdict(cfg) | {"hours_left": round(cfg.hours_left, 2)}
