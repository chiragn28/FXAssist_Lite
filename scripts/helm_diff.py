#!/usr/bin/env python3
"""K8S-09: show how each environment's rendered manifests differ from the base values.

Environment files must stay small overrides of values.yaml. This renders the chart with the
base values and with each override, and prints a unified diff, so drift is visible in review
and in CI. Exits non-zero if an override file grows past MAX_OVERRIDE_KEYS leaf values.
"""

from __future__ import annotations

import difflib
import subprocess
import sys
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parent.parent / "deploy" / "helm" / "fxassist"
ENVIRONMENTS = ["values-kind.yaml", "values-ci.yaml"]
MAX_OVERRIDE_KEYS = 12


def render(*values: Path) -> list[str]:
    args = ["helm", "template", "fxassist", str(CHART)]
    for v in values:
        args += ["-f", str(v)]
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.splitlines()  # noqa: S603


def leaves(node, prefix: str = "") -> list[str]:
    if isinstance(node, dict):
        return [k for key, v in node.items() for k in leaves(v, f"{prefix}{key}.")]
    return [prefix.rstrip(".")]


def main() -> int:
    base = render()
    status = 0
    for name in ENVIRONMENTS:
        path = CHART / name
        keys = leaves(yaml.safe_load(path.read_text()) or {})
        diff = list(difflib.unified_diff(base, render(path), "base", name, lineterm="", n=1))
        changed = sum(1 for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---"))
        print(f"== {name}: {len(keys)} override(s), {changed} rendered line(s) differ")
        print("\n".join(diff) or "  (no difference)")
        if len(keys) > MAX_OVERRIDE_KEYS:
            print(
                f"  {name} overrides {len(keys)} values (> {MAX_OVERRIDE_KEYS}): move shared settings to values.yaml"
            )
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
