"""The CI workflows keep their promises (ADR-015): CI-02, CI-03, CI-04, CI-05."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads the key `on` as True
    return data


@pytest.mark.parametrize("path", WORKFLOWS, ids=[p.name for p in WORKFLOWS])
def test_ci02_ci05_no_secrets_no_gpu_read_only(path: Path) -> None:
    text = path.read_text()
    assert "secrets." not in text, "CI-02: fork PRs get no secrets, so CI must not need any"
    assert "pull_request_target" not in text  # would run fork code with write access
    workflow = load(path)
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert job["runs-on"].startswith("ubuntu-"), "CI-05: standard runners only, no GPU"
        assert "timeout-minutes" in job


def test_ci_runs_on_pull_requests_including_forks() -> None:
    ci = load(ROOT / ".github" / "workflows" / "ci.yml")
    assert "pull_request" in ci["on"]


def test_ci05_nothing_calls_a_real_model_or_paid_api() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for forbidden in ("openai.com", "anthropic.com", "huggingface.co", "ollama", "nvidia"):
        assert forbidden not in text.lower(), forbidden
    adapter_tests = (ROOT / "services" / "agent" / "tests" / "test_llm_adapter.py").read_text()
    assert 'os.environ.get("CI") == "true"' in adapter_tests  # the Ollama contract test skips


def test_downloaded_tools_are_checksum_verified() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    downloads = len(re.findall(r"curl -fsSLO \"https://[^\"]+(?:tar\.gz|amd64|kubectl)\"", text))
    checks = text.count("sha256sum --check")
    assert downloads and checks >= 6, (downloads, checks)


def test_ci03_lockfile_check_and_weekly_drift_report() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "uv lock --check" in ci
    drift = load(ROOT / ".github" / "workflows" / "dependencies.yml")
    assert (
        drift["on"]["schedule"]
        and "uv lock --upgrade" in (ROOT / ".github/workflows/dependencies.yml").read_text()
    )


def test_ci04_images_are_built_budgeted_and_scanned() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "make images" in ci and "make image-budget" in ci
    assert "--exit-code 0" in ci  # vulnerability scan reports, does not block (SAF-08)


def test_actions_are_pinned_to_tags_that_exist() -> None:
    """setup-uv has no floating major tag: `@v10` broke the first GitHub run (2026-10-07)."""
    for path in WORKFLOWS:
        for ref in re.findall(r"uses: astral-sh/setup-uv@(\S+)", path.read_text()):
            assert re.fullmatch(r"v\d+\.\d+\.\d+", ref), f"{path.name}: setup-uv@{ref}"
