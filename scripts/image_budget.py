#!/usr/bin/env python3
"""Image size budgets (Phase 4, CI-04). Fails if a built image is larger than its budget.

Size = `docker image inspect .Size`. With Docker's containerd image store this is the
compressed content, i.e. what a pull downloads (checked 2026-10-07: it equals the size of
`docker save`, which gzip cannot shrink further). `docker images` shows a larger number that
includes the unpacked copy. Budgets leave about 15% headroom over the size measured when they
were set; raising one should be a deliberate, reviewed change.
"""

from __future__ import annotations

import json
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
    out = subprocess.run(  # noqa: S603
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,  # noqa: S607
    )
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)[0]["Size"] / 1e6


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
