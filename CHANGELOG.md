# Changelog

Notable changes per phase. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Phase 1: RAG core (2026-10-06)

### Added
- ADR-022: Ollama runs as a non-root compose container (`llm` profile), with the GPU added by `compose.gpu.yaml` when `nvidia-smi` works. `make up` starts it, `make pull-model` downloads `qwen2.5:3b-instruct`, `make up-lite` leaves it out.
- ADR-023: embeddings through fastembed (ONNX Runtime) instead of PyTorch.
- Corpus of 26 public documents (CFTC, ESMA, ASIC, FCA, Wikipedia) in `data/sources.yaml`, with licence terms read from each publisher; `data/SOURCES.md` generated from it. Three sources excluded because they block non-browser clients or need permission.
- `services/agent` (`fxassist` command): fetch with atomic writes and a report; PDF/HTML/Wikipedia extraction with boilerplate removal and an English-prose check; content-hash chunk IDs; Qdrant collection carrying the embedding model and corpus version; LangGraph agent (guard, retrieve, grade, rewrite, generate, validate); citation and number validation; step and time caps.
- Make targets `fetch`, `ingest`, `ask`, `eval`, `experiment`, `sources-md`, `pull-model`, `up-lite`.
- Evaluation set: 39 questions, 10 injection attacks, 3 planted-excerpt scenarios; scoring script; retrieval experiment.
- 84 offline unit tests for the agent (in-memory Qdrant, fake embedder, scripted fake LLM).

### Changed
- `make bootstrap` now checks Ollama, the model and the GPU as warnings with fix commands (ENV-03), and requires Phase 1 by default.
- Lint: E501 disabled (the formatter wraps code; long lines left are prompts and regexes).

### Fixed during the phase (found by tests or the eval)
- fastembed's `passage_embed` takes `batch_size` as a keyword; the first ingestion failed every document (and proved DAT-07: the run still finished with a report).
- PDF header detection looked at 3 edge lines; the FCA header is 5 lines tall.
- The guard flagged "What is a trading signal?" as an advice request.
- The model sometimes writes "Insufficient context" in prose or as `[Insufficient_context]`; now recognised.
- Injected sentences appended after the last citation (eval items i09, c02) are now removed by the validator, including when the model puts the citation after the full stop.

### Fixed (after Phase 0)
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
