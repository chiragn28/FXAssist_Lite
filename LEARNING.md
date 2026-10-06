# Learning log

One section per phase: the concepts used, why the design choices were made, and interview questions I should now be able to answer. Written for someone who knows data engineering but not LLM infrastructure.

---

## Phase 0: Scaffold

### Concepts and decisions

- **Reproducibility starts with a lockfile.** `pyproject.toml` says which packages I want (`pytest>=9.1.1`). `uv.lock` records the exact version and file hash of every package, including dependencies of dependencies. CI and Docker install from the lockfile with `uv sync --locked`, so "works on my machine" becomes "works on every machine". `uv lock --check` fails if someone edited `pyproject.toml` without re-locking (ADR-021, CI-03).

- **Line endings are a real failure mode on Windows.** Windows uses CRLF (`\r\n`), Linux uses LF (`\n`). A bash script with CRLF fails with `$'\r': command not found`. `.gitattributes` with `eol=lf` makes git write LF no matter how the machine is configured. A test also scans every committed file for CRLF (ENV-01).

- **Pre-commit hooks stop mistakes before they reach history.** Once a secret is in a git commit, deleting the file doesn't remove it: it stays in history and must be rotated. Gitleaks runs on every commit and looks for token patterns. Its hook only sees *staged* changes, so CI also scans the *full history* (SAF-07). I checked that it works by planting a fake GitHub token in a throwaway repo: gitleaks found it and exited with code 1.

- **Containers should not run as root.** If an attacker breaks out of a process running as root inside a container, they hold root's powers. Each compose service sets a non-root `user`, drops all Linux *capabilities* (fine-grained root powers such as binding low ports or changing file owners) and sets `no-new-privileges`. Verified: `CapEff` is all zeros in every container.

- **Healthchecks turn "started" into "ready".** A container can be running while the database inside it is still initialising. Each service has a healthcheck, and `docker compose up --wait` blocks until all report healthy. The same idea becomes Kubernetes readiness probes in Phase 4 (DEP-04, K8S-01).

- **Configuration through environment variables.** Ports and the bind address come from `.env` (ENV-04). This was tested for real: a local PostgreSQL already held port 5432, and setting `FXA_POSTGRES_PORT=55432` fixed it without editing any file. Binding to `127.0.0.1` keeps the services off the local network.

- **Memory budgets have to be explicit.** Every service has a `mem_limit`, so the total RAM the stack needs is known in advance and a runaway process gets killed instead of freezing the laptop. A test checks that the "lite" set fits the documented 4 GiB minimum (ENV-02).

- **Read the release notes of what you pin.** PostgreSQL 18 moved its data directory. A compose file copied from a PostgreSQL 17 tutorial mounts `/var/lib/postgresql/data` and silently loses data on every restart. Checking the official Dockerfile caught this; a restart test confirmed that data now survives.

### Interview questions I can now answer

1. *Why does a lockfile matter for an ML platform, and how do you detect lockfile drift in CI?*
   Without one, the same commit can install different library versions on different days; for fast-moving libraries like vLLM or LangChain, that changes behaviour. Install with `uv sync --locked` and fail CI on `uv lock --check`.
2. *A secret was committed and pushed. What do you do?*
   Rotate the secret first: assume it is compromised. Then remove it from history if needed, and prevent a repeat with a pre-commit scan plus a full-history scan in CI.
3. *What is the difference between a container that is running and one that is ready, and why does it matter for deployments?*
   Running means the process started; ready means it can serve requests. Sending traffic before readiness causes errors during startups and rolling updates. Healthchecks in Compose and readiness probes in Kubernetes gate traffic on readiness.
