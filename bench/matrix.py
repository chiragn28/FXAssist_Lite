"""The experiment matrix for the GPU lab (ADR-017), in the order it should run (GPU-06).

Each experiment is one vLLM server configuration plus the load cells measured on it. Knob
experiments change exactly one server setting relative to their variant's baseline (BEN-07;
checked by `check_one_variable` and a test). FP16 and AWQ use the same prompts, load and
settings, and differ only in the model (BEN-09).

Priority order (cut from the bottom when GPU hours run short, GPU-06):
  1 smoke test           10 minutes, before anything else (GPU-01)
  2 fp16 baseline        concurrency 1/4/16/32 x short/long
  3 awq baseline         the same cells
  4 eval fp16, eval awq  answer quality with the real model (ADR-016)
  5 knob experiments     one setting at a time, at concurrency 16
  6 OOM drill            induced, to write the runbook (GPU-03)
  7 stretch              7B AWQ, tensor parallel (Phase 7)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .harness import Cell

MODELS = {
    # Verified on huggingface.co 2026-10-06: not gated; Qwen Research License (non-commercial).
    "fp16": {"repo": "Qwen/Qwen2.5-3B-Instruct", "quantization": None},
    "awq": {"repo": "Qwen/Qwen2.5-3B-Instruct-AWQ", "quantization": "awq"},
}
STRETCH_MODELS = {
    # Apache-2.0. Only if hours remain (ADR-005).
    "awq-7b": {"repo": "Qwen/Qwen2.5-7B-Instruct-AWQ", "quantization": "awq"},
}

# The server settings every experiment starts from. float16 is required on a T4: it has no
# bfloat16, and the FP16 model's config.json says bfloat16 (GPU-02).
BASE_SERVER: dict[str, Any] = {
    "dtype": "float16",
    "max_model_len": 4096,
    "gpu_memory_utilization": 0.90,
    "max_num_seqs": 32,
    "enable_prefix_caching": False,  # off by default here, measured separately (BEN-02)
    "tensor_parallel_size": 1,
}


@dataclass(frozen=True)
class Experiment:
    name: str
    variant: str
    priority: int
    server: dict[str, Any] = field(default_factory=dict)  # overrides of BASE_SERVER
    cells: tuple[Cell, ...] = ()

    @property
    def server_config(self) -> dict[str, Any]:
        model = (MODELS | STRETCH_MODELS)[self.variant]
        return {
            **BASE_SERVER,
            **self.server,
            "model": model["repo"],
            "quantization": model["quantization"],
        }


def _requests_for(concurrency: int) -> int:
    return max(12, 4 * concurrency)  # enough for p95 at high concurrency, cheap at c=1


def load_cells(experiment: str, variant: str, concurrencies, prompt_sets) -> tuple[Cell, ...]:
    return tuple(
        Cell(experiment, variant, c, p, requests=_requests_for(c))
        for p in prompt_sets
        for c in concurrencies
    )


SWEEP = (1, 4, 16, 32)
KNOB_LOAD = dict(concurrencies=(16,), prompt_sets=("short", "long"))

EXPERIMENTS: list[Experiment] = [
    Experiment(
        "baseline", "fp16", 2, cells=load_cells("baseline", "fp16", SWEEP, ("short", "long"))
    ),
    Experiment("baseline", "awq", 3, cells=load_cells("baseline", "awq", SWEEP, ("short", "long"))),
    Experiment(
        "prefix-caching-on",
        "fp16",
        5,
        {"enable_prefix_caching": True},
        load_cells("prefix-caching-on", "fp16", **KNOB_LOAD),
    ),
    Experiment(
        "max-num-seqs-8",
        "fp16",
        5,
        {"max_num_seqs": 8},
        load_cells("max-num-seqs-8", "fp16", **KNOB_LOAD),
    ),
    Experiment(
        "gpu-mem-0.80",
        "fp16",
        5,
        {"gpu_memory_utilization": 0.80},
        load_cells("gpu-mem-0.80", "fp16", **KNOB_LOAD),
    ),
    Experiment(
        "max-model-len-2048",
        "fp16",
        5,
        {"max_model_len": 2048},
        load_cells("max-model-len-2048", "fp16", **KNOB_LOAD),
    ),
]

# Phase 7: two T4s with tensor parallelism vs one, same model and load (one variable).
# Skipped, with a recorded note, when fewer than 2 GPUs are assigned (GPU-07).
TENSOR_PARALLEL = [
    Experiment(
        "tensor-parallel-2",
        "fp16",
        7,
        {"tensor_parallel_size": 2},
        load_cells("tensor-parallel-2", "fp16", (1, 16, 32), ("short", "long")),
    ),
]
EXPERIMENTS += TENSOR_PARALLEL

# GPU-03: settings chosen to fail, to see and record what an out-of-memory looks like.
OOM_DRILL = Experiment(
    "oom-drill",
    "fp16",
    6,
    {"gpu_memory_utilization": 0.99, "max_model_len": 32768, "max_num_seqs": 256},
)


def check_one_variable(experiments: list[Experiment]) -> None:
    """BEN-07: a knob experiment differs from its variant's baseline in exactly one setting."""
    for e in experiments:
        if e.name == "baseline":
            continue
        base = Experiment("baseline", e.variant, 0).server_config
        changed = {k for k in base if base[k] != e.server_config[k]}
        if len(changed) != 1:
            raise ValueError(f"{e.name}: changes {sorted(changed) or 'nothing'}, must change one")


def scaled_down(
    experiments: list[Experiment], requests: int = 4, repetitions: int = 2
) -> list[Experiment]:
    """A tiny version of the matrix for mock-mode dry runs."""
    return [
        replace(
            e,
            cells=tuple(
                replace(c, requests=requests, warmup=1, repetitions=repetitions, max_tokens=16)
                for c in e.cells[:2]
            ),
        )
        for e in experiments
    ]
