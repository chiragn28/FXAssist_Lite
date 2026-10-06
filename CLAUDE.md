# FXAssist Lite: instructions for Claude Code

The build contract is `CLAUDE_CODE_PROMPT.md`. The design is `ARCHITECTURE.md` (ADRs). The requirements are `EDGE_CASES.md`. Read all three before changing anything.

Rules that are easy to forget:
- Total cost $0. Never fabricate benchmark or eval numbers; missing results are `PENDING`.
- Never pin a version from memory: verify it and record it in `docs/VERSIONS.md` with the date.
- Follow the ADRs. To change one, propose a new ADR and stop for approval.
- Reference edge-case IDs (e.g. `ENV-04`) in tests and commit messages, and update the Status column in `EDGE_CASES.md`.
- End every phase with a `LEARNING.md` section and a `CHANGELOG.md` entry, then stop for the user's "go".
- Containers run as non-root. LF line endings. No secrets in git.

Commands: `make help`, `make check` (lint, tests, lockfile), `make up` / `make down`.
Current phase: **5 complete**; Phases 6-7 need the user's Kaggle runs (then `make report`); Phase 8 is documentation. Raise `PHASE ?=` in the Makefile when a phase is done.
The repo lives in WSL2 at `~/fxassist_lite` (ADR-019). Agent tests: `uv run pytest services/agent/tests` (offline).
