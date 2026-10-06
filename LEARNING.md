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

---

## Phase 1: RAG core

### Concepts and decisions

- **RAG is a data pipeline first.** Most of the work was ingestion: download (with atomic writes so a failed download never leaves half a file), extract text (PDF page by page, HTML main content only, Wikipedia via its API), strip repeated headers and footers, split into chunks, embed, store. It is the same shape as an ETL job: idempotent, resumable, with a report of what failed and why.

- **Stable IDs make ingestion idempotent.** Each chunk's ID is a hash of its document and its text. Running ingestion twice upserts the same IDs, so nothing is duplicated, and only new chunks are embedded: the second run took 7.8 s instead of 2 min 14 s. When a document changes, chunks whose IDs disappeared are deleted, and the *corpus version* (a hash of all chunk IDs) changes. The Phase 2 cache puts the corpus version in its key, so stale answers expire by themselves (ADR-010).

- **Vectors from different models cannot be compared, and nothing warns you.** If the embedding model changes, search still returns results; they are just meaningless. So the collection records the model, runtime and dimension, and the agent refuses to start on a mismatch (DAT-10). Same reason for ADR-023: even the same model run through ONNX instead of PyTorch gives slightly different numbers.

- **Retrieval scores are relative, not absolute.** bge-small scores almost everything in the corpus between 0.7 and 0.9, so a fixed "relevance threshold" barely filters. The relevance decision is made by the grader (a model call), and the threshold only catches clearly off-topic questions (sourdough bread scored 0.42).

- **One long document can crowd out the answer.** "What leverage limits apply to retail CFD clients in the EU?" abstained because a 50-page FCA paper filled all five retrieval slots and the ESMA text with the answer ranked 13th. Capping each document at 2 excerpts fixed it. The experiment below shows the cap helps on exactly that question and costs nothing elsewhere; with one question of evidence, that is a sensible default, not a proven law.

- **An agent is a state machine with exits.** LangGraph makes each step a node and each decision an edge: guard, retrieve, grade, optional rewrite, generate, validate. Every path ends, either with an answer or with a deliberate refusal. Hard caps (12 steps, 120 s) mean even a bug cannot loop forever (RET-09).

- **Safety in three layers, cheapest first.** (1) Rules in code catch vague questions, advice requests, prompt-extraction and obvious injections before any model call: deterministic, free, testable. (2) The prompt treats excerpts and the question as untrusted data inside tags that the data cannot close. (3) Code checks the answer: citations must point to real excerpts, numbers must appear in the cited text, and anything written after the last citation is removed. Layer 3 caught two attacks that layers 1 and 2 missed (eval items i09 and c02).

- **Measure, then change one thing.** The first eval found 7 failures. Each one got a diagnosis before a fix: two were my eval being too strict, two were real bugs in my validator (found only because a real model produced unusual formatting), and the rest are limits of a 4-bit 3B model. Unit tests with a fake LLM check the control flow; the eval with the real model checks behaviour. You need both.

### Experiment: chunk size x top-k (DAT-09)

Retrieval only (no LLM), 22 answerable questions, bge-small via fastembed, overlap 15%, run on 2026-10-06 with `make experiment`. Hit rate: an expected source is in the top-k. MRR: mean of 1/rank of the first excerpt from an expected source. Fact hit rate: the retrieved text contains the expected fact (11 questions with a checkable fact such as "30:1").

| Chunk size (chars) | Chunks | Per-doc cap | Top-k | Hit rate | MRR | Fact hit rate |
|---|---|---|---|---|---|---|
| 500 | 1387 | off | 3 | 95% | 0.88 | 91% |
| 500 | 1387 | off | 5 | 95% | 0.88 | 100% |
| 500 | 1387 | 2 | 3 | 95% | 0.88 | 82% |
| 500 | 1387 | 2 | 5 | 95% | 0.88 | 91% |
| 1000 | 727 | off | 3 | 95% | 0.91 | 100% |
| 1000 | 727 | off | 5 | 95% | 0.91 | 100% |
| 1000 | 727 | 2 | 3 | 95% | 0.91 | 100% |
| **1000** | **727** | **2** | **5** | **100%** | **0.92** | **100%** |
| 1500 | 502 | off | 5 | 95% | 0.89 | 100% |
| 1500 | 502 | 2 | 5 | 95% | 0.89 | 100% |
| 1500 | 502 | 2 | 8 | 100% | 0.89 | 100% |

(Top-k 8 rows omitted where identical to top-k 5; the full table is printed by `make experiment`.)

What it says:
- **500-character chunks cut facts in half**: the fact hit rate drops to 82 to 91% because a sentence like "30:1 for major currency pairs" ends up split from its context.
- **1000 characters ranks best** (MRR 0.92) and keeps every fact. 1500 is close but ranks slightly worse: bigger chunks dilute the match.
- **The per-document cap** only changes one question (a21, the EU one), and that question is why it exists. On 22 questions this is weak evidence; a larger eval set could overturn it.
- My first version of this experiment measured only "right document retrieved" and scored 100% everywhere. **A metric that never moves tells you nothing**; checking for the actual fact made the differences visible.

Defaults chosen: chunk size 1000, overlap 150, top-k 5, at most 2 excerpts per document.

### Local evaluation (development numbers, not results)

`make eval` with Ollama `qwen2.5:3b-instruct` (4-bit) on an RTX 3060 Laptop GPU, 2026-10-06, final code:

| Measure | Result |
|---|---|
| Retrieval hit rate (answerable + ambiguous) | 22/22 |
| Answered (answerable + ambiguous) | 19/22 |
| Citation correctness (of answered) | 18/19 |
| Abstention accuracy (unanswerable, off-domain, vague) | 11/11 |
| Safety (4 advice, 2 reveal, 10 injection attacks) | 16/16 |
| Planted-excerpt scenarios (RET-02, RET-03, SAF-03) | 3/3 |
| Overall | 48/52 |
| Latency p50 / p95 (questions reaching the model) | 0.9 s / 1.5 s |

Cautions, in order of importance:
1. **These are not reported results.** ADR-016: only the GPU lab with the same evaluation counts. This is a 4-bit model on a laptop.
2. **The small model is unreliable on leverage questions.** Across 4 runs, a02, a18, a21 and m01 flipped between pass and fail. Typical error: "ESMA sets 20:1 for major pairs" (it is 30:1; 20:1 is the limit for non-major pairs in the same excerpt). The number check cannot catch this because 20:1 *is* in the cited text. Comparing FP16 and AWQ in Phase 6 will show how much of this is quantisation.
3. **Output depends on what ran before.** a21 failed inside the eval run but passed 6 times out of 6 on its own. The likely cause is Ollama reusing cached prompt prefixes plus non-deterministic GPU arithmetic, even at temperature 0. The eval now records the rejected text so the next failure can be diagnosed.
4. **22 answerable questions is a small sample.** One question moves a rate by about 5 points.

### Interview questions I can now answer

1. *How do you make document ingestion idempotent and keep the vector store in sync when documents change?*
   Derive chunk IDs from content (hash of document ID + chunk text) and upsert by ID. Before ingesting a document, read its existing IDs; after, delete the ones that are no longer produced. Track a corpus version (a hash of all IDs) and put it in cache keys so answers built on old content expire automatically.
2. *Your RAG system gives a confident wrong number. Where do you look, and what can you add?*
   First check whether retrieval found the right text (retrieval hit rate, fact hit rate). If it did, the model misread it, so generation is the problem. Add output checks: citations must point to retrieved excerpts, numbers must appear in the cited excerpt, and uncited claims are removed. Be honest that this catches invented numbers but not a real number attributed to the wrong thing; for that you need a stronger model or a per-claim check.
3. *How do you defend a RAG system against prompt injection?*
   In layers: rule-based checks on the input, a prompt that treats retrieved text and the question as data inside delimiters the data cannot close, and checks in code on the output (validated citations, numbers backed by sources, nothing after the last citation). Test it with a fixed attack set that includes attacks designed to get past the first layer, and measure the result.
