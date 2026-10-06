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
- **Chunk:** a piece of a document (here about 1000 characters) that is embedded and retrieved on its own. *(Phase 1)*
- **Chunk overlap:** characters repeated between neighbouring chunks so a sentence cut at a boundary still appears whole in one of them. *(Phase 1)*
- **Cosine similarity:** how closely two embeddings point in the same direction, from -1 to 1; the search "score". bge-small packs related text into a narrow band (0.7 to 0.9), so absolute scores say little on their own. *(Phase 1)*
- **Top-k:** how many of the best-scoring chunks are passed on. *(Phase 1)*
- **Vector database / collection:** Qdrant stores vectors plus their text and metadata ("payload") in a collection and finds the nearest ones to a query vector. *(Phase 1)*
- **Collection metadata:** key-value facts stored on the collection itself; here the embedding model, dimension and corpus version (DAT-10). *(Phase 1)*
- **Corpus version:** a hash of every chunk ID; it changes whenever any document's content changes, so caches keyed on it go stale automatically (DAT-03). *(Phase 1)*
- **Upsert / idempotent ingestion:** insert-or-replace by ID. With IDs derived from content, running ingestion twice changes nothing (DAT-02). *(Phase 1)*
- **Boilerplate:** text repeated on every page (headers, footers, page numbers, menus) that adds noise to retrieval (DAT-05). *(Phase 1)*
- **Near-duplicate de-duplication:** dropping a retrieved chunk that is almost identical to a better-ranked one (RET-10). *(Phase 1)*
- **Per-document cap (diversity):** at most N chunks from one document in the top-k, so one long document cannot crowd out the rest. *(Phase 1)*
- **Hit rate / MRR / fact hit rate:** retrieval metrics. Hit rate: share of questions where a correct document is in the top-k. MRR (mean reciprocal rank): average of 1/rank of the first correct result. Fact hit rate: share where the retrieved text actually contains the answer. *(Phase 1)*
- **ONNX Runtime:** an engine that runs exported neural networks without PyTorch; fastembed uses it (ADR-023). *(Phase 1)*

## Agent and generation

- **LangGraph state graph:** the agent as nodes (steps) and edges (which step comes next), sharing one state object. Conditional edges choose the next node from the state. *(Phase 1)*
- **Recursion limit:** LangGraph's cap on how many node runs one request may take, which stops loops (RET-09). *(Phase 1)*
- **Grader:** a model call that judges which retrieved excerpts actually help answer the question. *(Phase 1)*
- **Query rewrite:** asking the model to rephrase the question as a search query and retrieving again, once. *(Phase 1)*
- **Abstention:** deliberately answering "I don't have enough information" instead of guessing (RET-01). *(Phase 1)*
- **Citation validation:** checking in code that every citation points to an excerpt the model was given, that numbers appear in the cited text, and that no claim follows the last citation (RET-08, SAF-03). *(Phase 1)*
- **Prompt injection:** text (in the question or in a retrieved document) that tries to override the system's instructions (RET-03, SAF-04). *(Phase 1)*
- **Context window:** the most tokens a model can read plus write in one request; 4096 for Ollama's default. *(Phase 1)*
- **Token:** the unit models read and write; roughly three-quarters of an English word. *(Phase 1)*
- **Q4 quantisation (GGUF Q4_K_M):** Ollama's default 4-bit format; much smaller and faster than FP16, with some quality loss. Different from the AWQ format used in the GPU lab. *(Phase 1)*

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
