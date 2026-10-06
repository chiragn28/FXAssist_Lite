"""Static checks on deploy/compose/compose.yaml (ENV-02, ENV-04, OBS-04, SAF-08, DEP-04)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "deploy/compose/compose.yaml").read_text())
SERVICES = COMPOSE["services"]

# ENV-02: documented minimum Docker memory for the lite profile (README, bootstrap.sh).
LITE_BUDGET_MIB = 4 * 1024
# Headroom for commands run beside the stack (ingestion embeds on the host). The gateway and
# mock LLM are in the lite set since Phase 2, so their limits are counted directly.
APP_RESERVE_MIB = 1024

PORT_RE = re.compile(r"^\$\{FXA_BIND_ADDR:-127\.0\.0\.1\}:\$\{FXA_[A-Z_]+_PORT:-\d+\}:\d+$")


def to_mib(limit: str) -> int:
    number, unit = re.fullmatch(r"(\d+)([mg])", limit.lower()).groups()
    return int(number) * (1024 if unit == "g" else 1)


@pytest.mark.parametrize("name", SERVICES)
def test_env04_ports_come_from_env(name: str) -> None:
    """ENV-04: every host port is overridable, and bound to localhost by default (OBS-04)."""
    for mapping in SERVICES[name].get("ports", []):
        assert PORT_RE.match(mapping), f"{name}: {mapping!r} must use FXA_* port and bind vars"


def test_env04_every_variable_is_documented() -> None:
    """ENV-04: each FXA_* variable used by compose is listed in .env.example."""
    used = set(re.findall(r"\$\{(FXA_[A-Z_]+)", (ROOT / "deploy/compose/compose.yaml").read_text()))
    documented = set(re.findall(r"^(FXA_[A-Z_]+)=", (ROOT / ".env.example").read_text(), re.M))
    assert used - documented == set()


def test_env04_default_ports_are_unique() -> None:
    defaults = re.findall(r"_PORT:-(\d+)\}", (ROOT / "deploy/compose/compose.yaml").read_text())
    assert len(defaults) == len(set(defaults))


@pytest.mark.parametrize("name", SERVICES)
def test_env02_memory_limit_set(name: str) -> None:
    """ENV-02: without limits the RAM budget is unknowable."""
    assert "mem_limit" in SERVICES[name], f"{name} has no mem_limit"


def test_env02_lite_profile_fits_budget() -> None:
    """ENV-02: services with no profile form the lite set and must fit the documented minimum."""
    lite = [s for s in SERVICES.values() if not s.get("profiles")]
    total = sum(to_mib(s["mem_limit"]) for s in lite)
    assert total + APP_RESERVE_MIB <= LITE_BUDGET_MIB, f"lite set uses {total} MiB"


@pytest.mark.parametrize("name", SERVICES)
def test_saf08_runs_as_non_root(name: str) -> None:
    """SAF-08 / rule 8: containers run as a non-root user without extra capabilities."""
    service = SERVICES[name]
    user = str(service.get("user", ""))
    assert user and not user.startswith(("0", "root")), f"{name} must set a non-root user"
    assert "ALL" in service.get("cap_drop", [])
    assert "no-new-privileges:true" in service.get("security_opt", [])


def dockerfile_of(service: dict) -> Path:
    build = service["build"]
    return ROOT / build["context"] / build.get("dockerfile", "Dockerfile")


def base_images(service: dict) -> list[str]:
    """The pinned upstream images: `image`, or every FROM in the service's Dockerfile."""
    if "build" not in service:
        return [service["image"]]
    text = dockerfile_of(service).read_text()
    stages = set(re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.M | re.I))
    froms = [f for f in re.findall(r"^FROM\s+(\S+)", text, re.M) if f not in stages]
    assert froms, f"{dockerfile_of(service)}: no FROM line"
    return froms


@pytest.mark.parametrize("name", SERVICES)
def test_images_pinned_to_exact_version(name: str) -> None:
    """Rule 3: no floating tags; every version is recorded in docs/VERSIONS.md."""
    for image in base_images(SERVICES[name]):
        repo, _, tag = image.rpartition(":")
        # PostgreSQL releases are major.minor since v10, so "18.6" is already an exact release.
        pattern = r"\d+\.\d+$" if repo == "postgres" else r"v?\d+\.\d+\.\d+"
        assert re.match(pattern, tag), f"{name}: {image} is not pinned to an exact release"
        assert image in (ROOT / "docs/VERSIONS.md").read_text(), f"{image} missing from VERSIONS.md"


@pytest.mark.parametrize("name", [n for n, s in SERVICES.items() if "build" in s])
def test_saf08_own_images_switch_to_a_non_root_user(name: str) -> None:
    text = dockerfile_of(SERVICES[name]).read_text()
    final_stage = text[text.rfind("\nFROM ") :]
    assert re.search(r"^USER\s+[1-9]\d*", final_stage, re.M), f"{name}: final stage runs as root"


def test_adr022_ollama_is_optional_and_gpu_is_an_override() -> None:
    """ADR-022: Ollama sits in the `llm` profile; the GPU request lives only in the override."""
    assert SERVICES["ollama"]["profiles"] == ["llm"]
    assert "deploy" not in SERVICES["ollama"]
    gpu = yaml.safe_load((ROOT / "deploy/compose/compose.gpu.yaml").read_text())
    devices = gpu["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]
    assert devices[0]["capabilities"] == ["gpu"]


def test_saf08_ollama_dockerfile_drops_root() -> None:
    dockerfile = (ROOT / "deploy/compose/ollama/Dockerfile").read_text()
    assert re.search(r"^USER\s+1000", dockerfile, re.M)


@pytest.mark.parametrize("name", SERVICES)
def test_dep04_healthcheck_defined(name: str) -> None:
    """DEP-04 groundwork: `docker compose up --wait` can only gate on services with healthchecks."""
    assert "healthcheck" in SERVICES[name]
