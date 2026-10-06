"""scripts/bootstrap.sh must tell the user exactly what is missing (ENV-05)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bootstrap.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def run(*args: str, hide: str = "") -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FXA_BOOTSTRAP_HIDE": hide}
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_help_exits_zero() -> None:
    result = run("--help")
    assert result.returncode == 0
    assert "--phase" in result.stdout


def test_rejects_bad_phase() -> None:
    assert run("--phase", "four").returncode == 2


def test_missing_core_tools_fail_with_fix_commands() -> None:
    result = run(hide="uv,docker")
    assert result.returncode == 1
    assert "[MISS] uv" in result.stdout
    assert "astral.sh/uv/install.sh" in result.stdout
    assert "[MISS] docker CLI" in result.stdout
    assert "WSL integration" in result.stdout
    assert "Not ready:" in result.stdout


def test_later_phase_tools_warn_until_that_phase() -> None:
    """kind is only required from Phase 4; before that a missing kind is a warning."""
    early = run("--phase", "0", hide="kind")
    late = run("--phase", "4", hide="kind")
    assert "[WARN] kind (local Kubernetes) (needed from Phase 4)" in early.stdout
    assert "[MISS] kind (local Kubernetes)" in late.stdout
    assert late.returncode == 1
