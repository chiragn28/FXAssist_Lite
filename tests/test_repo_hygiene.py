"""Repo-level rules from Phase 0: line endings (ENV-01) and secret scanning (SAF-07)."""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".parquet", ".gz", ".zip"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    return [ROOT / p for p in out.decode().split("\0") if p]


def eol_attribute(path: str) -> str:
    out = subprocess.run(
        ["git", "check-attr", "eol", "--", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.strip().rsplit(": ", 1)[-1]


@pytest.mark.parametrize(
    "path",
    ["scripts/bootstrap.sh", "Makefile", "pyproject.toml", "deploy/compose/compose.yaml"],
)
def test_env01_gitattributes_forces_lf(path: str) -> None:
    """ENV-01: a Windows checkout with core.autocrlf=true must still get LF here."""
    assert eol_attribute(path) == "lf"


def test_env01_no_crlf_in_tracked_text_files() -> None:
    """ENV-01: nothing committed contains CRLF (catches files written before .gitattributes)."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in tracked_files()
        if p.is_file() and p.suffix not in BINARY_SUFFIXES and b"\r\n" in p.read_bytes()
    ]
    assert offenders == []


def test_saf07_precommit_runs_gitleaks() -> None:
    """SAF-07: secrets are scanned on every commit."""
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hook_ids = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}
    assert {"gitleaks", "detect-private-key"} <= hook_ids


def test_saf07_ci_scans_full_history() -> None:
    """SAF-07: the pre-commit hook only sees staged changes, so CI scans all of history."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout.get("with", {}).get("fetch-depth") == 0, "gitleaks needs full history"
    assert any("gitleaks git" in s.get("run", "") for s in steps)


def test_saf07_env_files_ignored_but_example_tracked() -> None:
    """SAF-07: .env holds real values and must never be committable; .env.example must be."""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=ROOT, check=False
    ).returncode
    assert ignored == 0, ".env must be git-ignored"
    assert (ROOT / ".env.example") in tracked_files()


def test_ruff_version_matches_precommit() -> None:
    """`make lint` and the commit hook must use the same ruff, or they disagree on formatting."""
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    rev = next(r["rev"] for r in config["repos"] if r["repo"].endswith("ruff-pre-commit"))
    assert f'"ruff=={rev.lstrip("v")}"' in (ROOT / "pyproject.toml").read_text()
