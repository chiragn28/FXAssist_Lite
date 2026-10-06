# Changelog

Notable changes per phase. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Fixed
- `make bootstrap` rounded Docker memory down to whole GiB, so a default 8 GB WSL2 VM (reported as 7.7 GiB) was flagged as too small for kind. Tiers now accept 90% of their nominal size and show one decimal (ENV-02).
- `make bootstrap` suggested `make install` even when it had already been run.

## Phase 0: Scaffold (2026-10-06)

### Added
- Repository layout for services, deploy, observability, bench, notebooks, eval, data, docs and results.
- `Makefile` with `help`, `bootstrap`, `install`, `lint`, `fmt`, `test`, `check`, `secrets-scan`, `up`, `up-full`, `down`, `down-volumes`, `ps`, `logs`. `ingest`, `ask`, `eval`, `kind-up`, `kind-deploy` and `demo` are placeholders that name their phase.
- `scripts/bootstrap.sh`: checks prerequisites (WSL2, repo location, git, make, uv, Python 3.11, Docker, Docker memory, disk, Ollama, kind, kubectl, helm) and prints the exact fix for each problem. It never installs anything.
- `uv` workspace root with a locked dev toolchain (ruff, pytest, pre-commit, PyYAML). New ADR-021.
- Pre-commit hooks: gitleaks secret scan, private-key detection, LF enforcement, large-file guard, ruff.
- `deploy/compose/compose.yaml` with Qdrant, Redis and PostgreSQL: pinned versions, non-root, no capabilities, localhost-only ports from env vars, memory limits, healthchecks.
- Minimal GitHub Actions CI: bootstrap on a clean runner, lockfile check, lint, tests, full-history gitleaks scan, compose validation.
- Tests for ENV-01, ENV-02, ENV-04, ENV-05 (bootstrap part), SAF-07 and SAF-08 (compose part).
- Docs: README skeleton, LEARNING (Phase 0), VERSIONS, GLOSSARY, SOURCES placeholder, CHANGELOG.

### Changed from the plan
- A minimal CI workflow was added in Phase 0 instead of Phase 5, because SAF-07 (a Phase 0 row) requires a CI secret scan. Phase 5 still builds the full pipeline.
