#!/usr/bin/env python3
"""Image size budgets (Phase 4, CI-04). Fails if a built image is larger than its budget.

Size = the gzip-compressed `docker save` stream, roughly what a pull downloads. It is measured
this way because `docker image inspect .Size` means different things in different Docker
setups: compressed content with the containerd image store (Docker Desktop), uncompressed with
the classic store (GitHub's runners). The first budgets used .Size and failed in CI (589 MB vs
234 MB for the same gateway image). Budgets leave about 15% headroom over the measured size;
raising one should be a deliberate, reviewed change.
"""

from __future__ import annotations

import subprocess
import sys

# image -> (budget in MB, what dominates its size)
BUDGETS = {
    # measured 2026-10-07: 234 MB, 51 MB, 47 MB
    "fxassist/gateway:0.4.0": (270, "ONNX Runtime, NumPy, the agent, the baked embedding model"),
    "fxassist/mock-llm:0.4.0": (60, "Python base, FastAPI, uvicorn"),
    "fxassist/watchdog:0.4.0": (55, "Python base, httpx"),
}


def size_mb(image: str) -> float | None:
    if subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, check=False
    ).returncode:
        return None
    save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
    gzip = subprocess.run(["gzip", "-6", "-c"], stdin=save.stdout, capture_output=True, check=True)
    save.wait()
    return len(gzip.stdout) / 1e6


def main() -> int:
    failed = 0
    for image, (budget, why) in BUDGETS.items():
        size = size_mb(image)
        if size is None:
            print(f"  [MISSING] {image}: not built (make images)")
            failed += 1
            continue
        ok = size <= budget
        failed += not ok
        print(f"  [{'OK' if ok else 'OVER'}] {image}: {size:.0f} MB of {budget} MB ({why})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
