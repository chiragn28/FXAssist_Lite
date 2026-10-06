# Glossary

Plain-language definitions of every term used in this project. Extends the glossary in [ARCHITECTURE.md](../ARCHITECTURE.md#6-glossary-for-me). New terms are added in the phase that introduces them.

## LLM and inference

- **Inference:** running a trained model to get answers.
- **vLLM:** a server that runs LLMs efficiently by batching requests and managing GPU memory.
- **KV cache:** memory holding what the model has already "read" for each request; it limits how many requests run at once.
- **Time to first token (TTFT):** delay before the first word appears; what users feel.
- **Throughput:** total tokens or requests handled per second.
- **Quantization (AWQ):** storing the model in fewer bits to save memory, usually with a small quality loss.
- **Tensor parallelism:** splitting one model across GPUs.

## Retrieval

- **RAG (retrieval-augmented generation):** retrieve relevant document chunks, then let the model answer using them.
- **Embedding:** a list of numbers representing the meaning of text.

## Platform and operations

- **Probe:** a check Kubernetes runs to decide if a container is alive or ready.
- **Helm chart:** a template package for deploying an app to Kubernetes.
- **Cardinality (metrics):** number of distinct label values; too many overwhelms Prometheus.
- **Healthcheck (Docker):** a command Docker runs inside a container at intervals to decide whether it is healthy. `docker compose up --wait` waits for every healthcheck to pass. *(Phase 0)*
- **Readiness vs liveness:** *ready* means "can take traffic now"; *live* means "the process is not stuck". A slow-starting service is live but not ready. *(Phase 0)*
- **Non-root container:** a container whose main process runs as an ordinary user, so a break-out gives the attacker fewer powers. *(Phase 0)*
- **Linux capabilities:** root's powers split into small pieces (bind low ports, change file owners, and so on). `cap_drop: [ALL]` removes them all. *(Phase 0)*
- **Bind address:** the network interface a port listens on. `127.0.0.1` means "this machine only"; `0.0.0.0` means "every network the machine is on". *(Phase 0)*
- **Compose profile:** a label that groups optional services; they start only when that profile is enabled (`COMPOSE_PROFILES=full`). *(Phase 0)*
- **Named volume:** Docker-managed storage that outlives the container, used for database files. *(Phase 0)*

## Developer tooling

- **Lockfile (`uv.lock`):** a file recording the exact version and hash of every installed package, so every install is identical. *(Phase 0)*
- **uv:** a fast Python tool that installs Python versions, creates virtual environments and manages the lockfile (ADR-021). *(Phase 0)*
- **Workspace (uv):** one repository holding several Python projects that share a single lockfile. *(Phase 0)*
- **Pre-commit hook:** a script git runs before each commit; if it fails, the commit is blocked. *(Phase 0)*
- **Secret scanning (gitleaks):** searching files and git history for patterns that look like passwords, tokens or keys. *(Phase 0)*
- **LF / CRLF:** line-ending styles. Linux uses LF (`\n`), Windows uses CRLF (`\r\n`). Shell scripts break with CRLF. *(Phase 0)*
- **`.gitattributes`:** per-path git settings; here it forces LF line endings. *(Phase 0)*
- **WSL2:** Windows Subsystem for Linux: a real Linux kernel running inside Windows. *(Phase 0)*
- **Idempotent:** safe to run twice; the second run changes nothing. *(Phase 0)*
