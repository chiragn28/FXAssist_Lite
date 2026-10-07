"""`make lab-dry-run`: the whole GPU-lab flow against the mock LLM, run the way Kaggle runs it.

The notebook executes inside Jupyter (papermill on Kaggle), where an event loop is already
running. The dry run does the same, so code that only works without a running loop fails here
on the laptop instead of on the GPU (the second Kaggle run failed that way, 2026-10-07).
"""

from __future__ import annotations

import asyncio

from . import lab, matrix


async def _as_in_a_notebook() -> None:
    cfg = lab.LabConfig.for_mode("mock")
    lab.check_environment(cfg)
    smoke = lab.smoke_test(cfg)
    lab.run_experiments(cfg, matrix.scaled_down(matrix.EXPERIMENTS), smoke)
    lab.oom_drill(cfg)
    print(lab.package(cfg))


if __name__ == "__main__":
    asyncio.run(_as_in_a_notebook())
