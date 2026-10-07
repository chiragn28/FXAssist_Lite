# Interview questions and model answers

Questions an LLMOps, ML platform or AI infrastructure interviewer could ask about FXAssist Lite, with model answers. Each answer explains the concept first, then ties it to what this repo actually does, and names the file that backs it.

`docs/INTERVIEW_NOTES.md` has the 1- and 5-minute pitch and a trade-off per ADR. This document goes deeper and wider. Read that one first.

## How to use this document

- **Say the answer in your own words.** The model answers are a reference, not a script. If you cannot explain a sentence, open the cited file and read the code or the result.
- **Lead with the concept, then the evidence.** Interviewers want to know you understand the general idea and that you have done it once for real.
- **State the limits before you are asked.** This project ran GPUs on free Kaggle T4s in a notebook and Kubernetes on kind on a laptop, without a GPU. No EKS, AWS GPU or production experience is claimed (`README.md`, "Honest limits").
- **Numbers have two labels.** "GPU lab" numbers come from `results/` (Kaggle, Tesla T4, vLLM 0.31.0, Qwen2.5-3B-Instruct, 2026-10-07). "Local" numbers are development measurements from `LEARNING.md` (laptop, Ollama 4-bit model, kind, mock LLM). Local numbers are not results. Where a number is simple arithmetic on a result, it says "derived".
- **Where the honest answer is "not done here"**, the answer says so and describes how you would do it.

Difficulty tags:

- **[basic]**: a concept every candidate for the role should explain clearly.
- **[intermediate]**: needs hands-on experience; expect follow-ups.
- **[advanced]**: design judgement, trade-offs, or reading real metrics to find a root cause.

## Contents

1. [LLM serving and vLLM](#1-llm-serving-and-vllm) (11 questions)
2. [Quantisation and GPU performance](#2-quantisation-and-gpu-performance) (7 questions)
3. [Benchmarking methodology](#3-benchmarking-methodology) (6 questions)
4. [RAG and retrieval](#4-rag-and-retrieval) (7 questions)
5. [Evaluation and LLM quality](#5-evaluation-and-llm-quality) (7 questions)
6. [Safety and guardrails](#6-safety-and-guardrails) (5 questions)
7. [API gateway and reliability](#7-api-gateway-and-reliability) (9 questions)
8. [Observability](#8-observability) (6 questions)
9. [Kubernetes and deployment](#9-kubernetes-and-deployment) (7 questions)
10. [CI/CD and supply chain](#10-cicd-and-supply-chain) (5 questions)
11. [Cost and capacity planning](#11-cost-and-capacity-planning) (4 questions)
12. [System design follow-ups](#12-system-design-follow-ups) (6 questions)

---

## 1. LLM serving and vLLM

### 1.1 What problem does PagedAttention solve? [basic]

Each running request needs a KV cache: the keys and values of every token it has seen, kept so the model does not recompute them at each step. Older servers reserved one contiguous block per request, sized for the maximum length, so most of it sat empty and memory fragmented. PagedAttention splits the KV cache into small fixed-size blocks and keeps a block table per request, like virtual memory pages in an operating system. Memory is handed out as a request grows, and blocks with identical content can be shared between requests (that is what makes prefix caching cheap). The result is that far more requests fit on one GPU at once. In this project vLLM reported the KV cache in tokens, not in requests: 199,744 tokens for FP16 on one T4, which it can share among any mix of request lengths (`results/BENCHMARKS.md`, GPU memory table).

### 1.2 What is continuous batching, and why does it matter for throughput? [basic]

Static batching waits for a batch to fill, runs it, and waits for the longest request to finish before starting new ones. Continuous batching schedules at every decode step: finished requests leave the batch and waiting ones join straight away. The GPU stays busy, and a short request is never stuck behind a long one. The benchmark shows the effect: FP16 throughput on short prompts rose from 123 tokens/s at 4 users to 629 tokens/s at 32 users, while each request's latency rose only from 4.16 s to 6.50 s (p50) (`results/BENCHMARKS.md`). A single request cannot use the GPU's compute; many requests batched together can.

### 1.3 How big is the KV cache per token for this model, and how do you calculate it? [intermediate]

The formula is 2 (key and value) x layers x KV heads x head dimension x bytes per value. For Qwen2.5-3B-Instruct that is 2 x 36 x 2 x 128 x 2 bytes = 36,864 bytes = 36 KiB per token in FP16. The small number of KV heads (2) is grouped-query attention, which keeps the cache small. Derived from that: one 4,096-token request needs about 144 MiB of KV cache. vLLM's own start-up logs matched the formula on every server: 6.86 GiB of KV memory held 199,744 tokens, which is 36.0 KiB per token (`docs/runbooks/gpu-oom.md`). One subtle point: the AWQ model's KV cache is still FP16, because quantising the weights does not shrink the cache.

### 1.4 Explain prefill and decode. Which one is compute bound, and which one is memory-bandwidth bound? [intermediate]

Prefill processes the whole prompt in one pass; it produces the KV cache and the first token. It is large matrix-times-matrix work, so it is limited by arithmetic (compute bound). Decode then generates one token per step per request; each step must read every weight from GPU memory to do a small amount of math, so it is limited by memory bandwidth. Time to first token is mostly queueing plus prefill; the time between tokens is decode. In the GPU lab, a single user's TTFT was 0.06 s on short prompts and 0.66 s on the RAG-sized prompts with about 1,500 tokens of context (FP16, `results/BENCHMARKS.md`): that difference is prefill. This split also explains the AWQ results in section 2.

### 1.5 What is the trade-off between TTFT and throughput? [intermediate]

More concurrent requests means more work per GPU step, so total throughput rises, but each request waits longer for its turn and each step takes longer. The service owner has to pick a point on that curve. FP16 on long prompts: at 1 user, TTFT p50 0.66 s and 18 tokens/s; at 32 users, TTFT p50 3.66 s, p95 17.17 s, and 135 tokens/s (`results/BENCHMARKS.md`). Throughput only grew from 121 to 135 tokens/s between 16 and 32 users, while p95 TTFT doubled. That flattening is the useful number: past it you add latency without adding capacity. For a chat product I would pick the concurrency where p95 TTFT still meets the target, and add replicas beyond that.

### 1.6 What does prefix caching do, and how much did it help here? [intermediate]

vLLM hashes KV-cache blocks by their token content. When a new request starts with the same tokens as an earlier one (a shared system prompt, the same document), it reuses those blocks and skips their prefill. In the knob experiment at 16 users, long-prompt TTFT p50 fell from 3.30 s to 0.20 s and throughput rose from 121 to 290 tokens/s (`results/BENCHMARK_NOTES.md`). But vLLM's own counters showed 96% of prompt tokens were cache hits (241,040 of 250,959), because the benchmark's long prompts reuse a handful of context blocks. That is a best case. Real RAG traffic retrieves different excerpts for each question, so only the system prompt would reliably hit. This is why prefix caching was off in every other cell (BEN-02) and why the result is reported with the caveat.

### 1.7 What do `max_num_seqs` and `gpu_memory_utilization` control? What happened when you changed them? [intermediate]

`max_num_seqs` caps how many requests run in one batch; the rest wait in vLLM's queue. `gpu_memory_utilization` is the share of GPU memory vLLM may plan for; after weights and activations, the rest becomes KV cache. Dropping `max_num_seqs` from 32 to 8 at 16 users raised short-prompt TTFT p50 from 0.29 s to 4.57 s and cut throughput from 408 to 228 tokens/s, with the KV cache only 6% full: pure queueing. Dropping utilisation from 0.90 to 0.80 shrank the KV cache from 199,744 to 157,328 tokens with no measurable speed change, because the cache never passed 21% use (`results/BENCHMARK_NOTES.md`). Lesson: a knob only matters when it is the bottleneck, and you need the server's metrics to know which one is.

### 1.8 Why does a vLLM server take so long to start, and what do you do about it in Kubernetes? [intermediate]

Start-up is weights loading, a profiling pass, `torch.compile` and CUDA graph capture. Measured over 11 starts: cold starts took 77 to 104 s, of which `torch.compile` was 22 to 27 s; warm starts with the compile cache reused took about 33 s; loading the weights took only 1 to 6 s (`docs/runbooks/slow-model-load.md`). CUDA graph capture took 5 to 11 s and grows with `max_num_seqs` (51 batch sizes captured at 256 sequences, against 11 at the default). The compile cache key includes the settings, so changing `max_model_len` or `max_num_seqs` forces a cold compile on every replica. In Kubernetes: a startup probe sized from the measured cold start plus a margin, the compile cache on a persistent volume, and rollouts that keep old replicas serving until new ones are ready. This project proved the startup-probe behaviour on kind with the gateway, not with a real model server.

### 1.9 Which attention backend does vLLM use on a T4, and how did you find out without spending GPU hours? [advanced]

The T4 is compute capability 7.5 (Turing). Reading the installed vLLM 0.31.0 source showed FlashAttention needs 8.0, and FlashInfer is deliberately switched off on 7.5 ("currently broken on SM75"), so the expected choice was the Triton backend (`docs/KAGGLE_PLAYBOOK.md`, section 1). The lab's smoke test runs first, tries the default backend, falls back to TRITON_ATTN explicitly if needed, and records which one ran. On the real run it chose TRITON_ATTN automatically (`results/BENCHMARKS.md`, Setup). The general lesson is to verify the platform before pinning it: "vLLM supports the T4" was true, but which kernels it gets matters for performance and for which features work. All of it was checked on the laptop, before the first GPU session (GPU-01).

### 1.10 Why float16 and not bfloat16? What is the risk? [intermediate]

Both are 16-bit formats. bfloat16 keeps float32's exponent range with less precision; float16 has more precision but a much smaller range, so very large activations can overflow. The T4 has no bfloat16 support. Qwen2.5-3B-Instruct's `config.json` says bfloat16, and `--dtype auto` would pick it, so every server in the lab passes `--dtype float16` explicitly and a test enforces it (GPU-02, `EDGE_CASES.md`). The risk is numerical: a model trained in bfloat16 can produce inf or NaN in float16 if some activation exceeds the range. Nothing like that showed up here, but the evaluation was not designed to detect it; on a new model I would check for NaN logits or garbage output in the smoke test.

### 1.11 What does "vLLM plans memory at start-up" mean for out-of-memory errors? [advanced]

vLLM takes `gpu_memory_utilization x total memory` as its budget, loads the weights, measures activation memory with a profiling pass, captures CUDA graphs, and gives everything left to the KV cache. It then queues requests instead of over-allocating. So an impossible configuration fails at start-up as a refusal, not as a crash under load. The OOM drill proved it by accident: settings chosen to fail (0.99 utilisation, 32,768-token max length, 256 sequences) started and served normally with 213,168 KV tokens (`docs/runbooks/gpu-oom.md`, `results/BENCHMARKS.md` section 7). `max_model_len` only requires one sequence to fit, and `max_num_seqs` is a ceiling, not a reservation. OOM under load usually means another process grabbed GPU memory after vLLM made its plan. A drill that would really fail (utilisation 0.35, smaller than the FP16 weights) is written in the runbook but was not run.

---

## 2. Quantisation and GPU performance

### 2.1 What is AWQ, and what does it cost? [basic]

AWQ (activation-aware weight quantisation) stores weights in 4 bits with a scale per group of weights, and protects the small share of weights that matter most for the activations. Activations and the KV cache stay in 16 bits; weights are unpacked on the fly during the matrix multiply. The benefits are less memory for weights and less data to read per decode step. The cost is some accuracy, and some extra arithmetic for unpacking. Here the AWQ model took 1.95 GiB against 5.79 GiB for FP16 (`results/BENCHMARKS.md`), and it passed fewer evaluation items: 42/52 against 46/52 (`results/EVAL_REPORT.md`).

### 2.2 AWQ was 2.3x faster at low load but only 1.07x faster with long prompts at 32 users. Why? [advanced]

At low load and with short prompts, the work is decode, which is memory-bandwidth bound: each step reads every weight once, and AWQ reads about a third of the bytes. So AWQ wins big: 284 against 123 tokens/s at 4 users on short prompts (2.3x). With RAG-sized prompts and many users, most of the GPU's time goes to prefill, which is compute bound. 4-bit weights do not reduce the arithmetic, and unpacking them adds some. So the gap closes: 144 against 135 tokens/s at 32 users on long prompts (1.07x), and TTFT p50 was about the same for both (3.66 s against 3.76 s) (`results/BENCHMARK_NOTES.md`, section 1). The general rule: quantisation speeds up the bandwidth-bound part of inference, not the compute-bound part.

### 2.3 Besides speed, what else did AWQ buy you? [intermediate]

Memory. Smaller weights leave more of the budget for the KV cache: 10.32 GiB (300,496 tokens) against 6.86 GiB (199,744 tokens), 1.5x more concurrent context on the same GPU (`results/BENCHMARKS.md`). vLLM's reported maximum concurrency at 4,096 tokens per request rose from 48.77x to 73.36x. That matters when the KV cache is the limit: long contexts or many users. In this benchmark the cache never passed 21% use, so the extra room was not needed. With longer contexts or a larger model, it would decide how many users fit on one GPU.

### 2.4 How do you decide whether a workload is memory-bandwidth bound or compute bound? [advanced]

Compare the work to the data moved. In decode, each step does a little math per weight read, so bandwidth is the limit. A rough single-stream ceiling is memory bandwidth divided by the bytes read per token, roughly the weight size. In prefill and large batches, many tokens share each weight read, so arithmetic becomes the limit. In practice, test it: change something that only affects bytes (quantisation) and see if speed changes. The AWQ results did exactly that: a big gain where decode dominates, almost none where prefill dominates. A profiler (Nsight) would confirm it directly; this project did not profile kernels.

### 2.5 You ran tensor parallelism on two T4s without peer-to-peer. What happened and why is it not 2x? [advanced]

Tensor parallelism splits every layer's weights across GPUs; after each layer, the GPUs combine partial results with an all-reduce. On this Kaggle host the T4s had no NVLink and no PCIe peer-to-peer. vLLM detected that, disabled its custom all-reduce, and used NCCL (2.29.7) through host memory (GPU-08, `results/tensor_parallel.md`). It still worked first time with 0 errors: 1.70x at 16 users and 1.60x at 32 users on short prompts, 1.82x and 1.81x on long prompts. It is not 2x because every layer's all-reduce over PCIe is overhead. Long prompts scale slightly better because they do more computation per byte communicated. The single-user ratio (2.7x) is flagged as unreliable because the single-GPU baseline cell looks throttled.

### 2.6 When is tensor parallelism worth it, and when would you use replicas instead? [intermediate]

Use tensor parallelism when the model does not fit on one GPU, or when single-request latency matters more than cost. Use data parallelism (independent replicas behind a load balancer) when the model fits, because replicas have no communication overhead and scale throughput almost linearly. A 3B model fits on one T4 comfortably, so on cost alone two replicas would beat TP=2 here. TP did cut first-token time: TTFT p50 dropped by about 30 to 45% (for example 3.30 s to 1.80 s at 16 users on long prompts), and it gave 564,928 KV tokens across both GPUs (`results/tensor_parallel.md`). On GPUs with NVLink the communication cost is much lower, so TP scales better; these PCIe numbers do not transfer to A100 or H100 systems.

### 2.7 One benchmark cell looked wrong. How did you handle it? [intermediate]

FP16, one user, short prompts reported 10 tokens/s with a range of 10 to 18. vLLM's own metrics showed the generation rate with one request sliding from 26.4 to 18.7 to 9.1 tokens/s within about a minute while GPU utilisation stayed at 100% (`results/BENCHMARK_NOTES.md`, section 5). Full load with falling speed looks like clock throttling on a passively cooled T4. It is not proven, because the lab sampled memory and utilisation but not clocks, temperature or power. So the cell is reported as measured, flagged as unreliable, and not used for comparisons; the long-prompt single-user figure is used instead. The fix for next time is to sample `clocks.sm`, `temperature.gpu`, `power.draw` and the throttle reasons alongside the other metrics.

---

## 3. Benchmarking methodology

### 3.1 How do you benchmark an LLM server so the numbers mean something? [basic]

Treat it as a controlled experiment. Call the model server directly with no answer cache in front. Use fixed, hashed prompt sets, a fixed output length, discarded warm-up requests, at least three repetitions with median and range, p50 and p95, errors counted separately, one variable changed per experiment, and every setting written into every result row. This project's rules are in `docs/BENCHMARK_METHOD.md` and enforced by tests BEN-01 to BEN-09 (`EDGE_CASES.md`). The output: 90 benchmark rows with 0 errors (`ARCHITECTURE.md`, JD matrix). Then say what the numbers do not show: a free T4 is not an H100.

### 3.2 Why discard warm-up requests? [basic]

The first requests to a fresh server pay one-off costs: connection setup, lazy initialisation, kernel compilation for new shapes, and filling caches. Counting them makes the first cell of every run look slower than it is. The harness sends a few warm-up requests per repetition and throws their results away (BEN-01, `bench/harness.py`). The same idea applies in production: a new replica should not get full traffic before it is warm, which is one reason for readiness probes.

### 3.3 Why fix the output length with `ignore_eos`? [intermediate]

Tokens per second depends on how many tokens each request generates. If one model stops at 40 tokens and another writes 128, their throughput numbers compare different work. The harness sends `max_tokens 128` with vLLM's `ignore_eos`, so every request generates exactly 128 tokens, and the smoke test checks that it really does (BEN-08, `docs/BENCHMARK_METHOD.md`). The trade-off: fixed 128-token outputs are shorter than many real answers, so absolute latencies are not what users would see (`results/BENCHMARK_NOTES.md`, Cautions).

### 3.4 Why report p50 and p95 rather than the mean? And what is the catch with p95 here? [intermediate]

Latency distributions have long tails. A mean hides the slow requests that users notice and complain about; p95 shows what the slowest 1 in 20 get. The harness uses the nearest-rank method and returns "no data" rather than zero when a cell has no successes (BEN-05). The catch: with 12 requests per cell, p95 is the 12th value, so it is simply the slowest request (`docs/BENCHMARK_METHOD.md`). Read small-cell p95 as "the worst case seen", not as a stable percentile.

### 3.5 Why must errors not count as fast successes? [basic]

A server that fails fast returns errors in milliseconds. If those count in the latency statistics, an overloaded server looks faster than a healthy one. The harness counts failures by type (non-200, broken stream, timeout, no content), reports the error rate, and leaves failed requests out of latency (BEN-06, `docs/BENCHMARK_METHOD.md`). The same trap exists in production dashboards: a circuit breaker turning slow timeouts into fast 503s improves average latency while the service is down.

### 3.6 How do you make sure an experiment changes only one thing? [intermediate]

Define a baseline and describe each experiment as a difference from it. Here, every knob experiment differs from the FP16 baseline in exactly one server setting, and `check_one_variable` refuses to run one that changes more (BEN-07, `bench/matrix.py`). FP16 and AWQ get the same prompts, load cells and settings, with only the model repository and `--quantization awq` different (BEN-09). Prefix caching is off everywhere except its own experiment, and the setting is in every row (BEN-02). With three repetitions, a difference smaller than the min-max ranges is not claimed: 0.80 against 0.90 utilisation was "no measurable change" for exactly that reason.

---

## 4. RAG and retrieval

### 4.1 Walk me through ingestion. What makes it production-like? [basic]

It is an ETL job: download, extract text (PDF page by page, HTML main content, Wikipedia through its API), strip repeated headers and footers, split into chunks, embed, upsert into Qdrant. It is idempotent, resumable and reports failures per document (`LEARNING.md`, Phase 1). Downloads are atomic, so a failure never leaves half a file (DAT-07); scanned PDFs with no text are skipped with a warning (DAT-01); non-English text is flagged, not silently embedded (DAT-06); large documents are embedded in bounded batches (DAT-04). The corpus is 26 public documents, 727 chunks (`results/EVAL_REPORT.md`). Licences are tracked in `data/SOURCES.md`, and documents that may not be redistributed are fetched, never committed (DAT-08).

### 4.2 How do you make ingestion idempotent and keep the vector store in sync when a document changes? [intermediate]

Derive each chunk's ID from its content: here a UUID5 of the source ID and the SHA-256 of the chunk text (`services/agent/src/fxassist_agent/chunking.py`). Upserting the same chunk twice writes the same ID, so nothing is duplicated, and unchanged chunks are not re-embedded. Locally, a second ingestion run took 7.8 s instead of 2 min 14 s (local, `LEARNING.md`). When a document changes, its old IDs that are no longer produced get deleted, and a corpus version (a hash of all chunk IDs) changes. That version is part of the answer-cache key, so answers built on old text expire by themselves (DAT-02, DAT-03, CAC-02).

### 4.3 How did you choose chunk size and top-k? [intermediate]

With an experiment, not a default from a tutorial. `make experiment` measured retrieval only, on 22 answerable questions, for chunk sizes 500, 1000 and 1500 characters, top-k 3, 5 and 8, with and without a per-document cap (local, `LEARNING.md` Phase 1, DAT-09). 500-character chunks split facts from their context (fact hit rate 82 to 91%); 1500 diluted the match slightly; 1000 characters with top-k 5 and at most 2 excerpts per document scored best (hit rate 100%, MRR 0.92, fact hit rate 100%). The chosen defaults are chunk size 1000, overlap 150, top-k 5, cap 2. On 22 questions this is weak evidence, and the write-up says so.

### 4.4 What is the difference between retrieval hit rate and fact hit rate, and why did it matter? [intermediate]

Retrieval hit rate asks: was an expected source document in the top-k? Fact hit rate asks: does the retrieved text actually contain the expected fact, such as "30:1"? A chunk can come from the right document and still miss the sentence with the answer. The first version of the chunking experiment measured only hit rate and scored 100% everywhere, so it could not tell the settings apart (local, `LEARNING.md`). Adding the fact check made the differences visible. The lesson applies to any metric: one that never moves tells you nothing.

### 4.5 Why cap the number of excerpts per document? [intermediate]

One long document can crowd out the answer. "What leverage limits apply to retail CFD clients in the EU?" abstained because a 50-page FCA paper filled all five slots and the ESMA text with the answer ranked 13th (local, `LEARNING.md`). Capping each document at 2 excerpts fixed it and changed nothing else in the experiment. Retrieval also over-fetches (top-k x 4 candidates), removes near-duplicates, then applies the cap (RET-10, `services/agent/src/fxassist_agent/graph.py`). This is a cheap form of result diversity; a re-ranker or MMR would be the next step.

### 4.6 What protects you from mixing embeddings from different models? [intermediate]

Nothing warns you by default. If the embedding model changes, search still returns results, but the similarities are meaningless. Here the Qdrant collection records the model name, runtime and dimension (`BAAI/bge-small-en-v1.5`, ONNX Runtime through fastembed, 384 dimensions), and the agent refuses to start on a mismatch with a clear error (DAT-10, ADR-023). Even the runtime matters: ONNX and PyTorch can differ in the last decimals, so vectors are never mixed across runtimes. For a real model upgrade, build a new collection, evaluate it, then switch an alias, rather than re-embedding in place.

### 4.7 Describe the agent graph. Why grading and rewriting, and why hard limits? [intermediate]

It is a LangGraph state machine: guard, retrieve, grade, optional rewrite, generate, validate (`services/agent/src/fxassist_agent/graph.py`). Guard applies cheap rules (advice, injection, vague questions) before any model call. Retrieve checks a similarity floor for off-domain questions. Grade asks the model which excerpts are relevant, because bge-small scores almost everything between 0.7 and 0.9 and a fixed threshold barely filters (local, `LEARNING.md`). If nothing is relevant, the question is rewritten as a search query once, then the agent abstains. Malformed grader output means "not relevant", never a crash (RET-07). Every path ends: there is a 12-step and 120-second cap, so even a bug cannot loop forever (RET-09).

---

## 5. Evaluation and LLM quality

### 5.1 How did you design the evaluation set? [basic]

52 hand-written items: 22 answerable or ambiguous questions with expected source documents and checkable facts; 11 that should not be answered (unanswerable, off-domain, vague); 16 safety items (4 advice requests, 2 prompt-extraction attempts, 10 injection attacks); and 3 planted-excerpt scenarios (conflicting sources, an instruction hidden in an excerpt, an invented number) (`results/EVAL_REPORT.md`). Metrics are retrieval hit rate, answered rate, citation correctness, content checks, abstention accuracy and safety. Including items that must not be answered is essential: a system that always answers would look great on answerable questions only. No paid judge model was used (ADR-016).

### 5.2 What were the GPU-lab results, and how do you read them? [intermediate]

FP16 passed 46/52 and AWQ 42/52; both had retrieval 22/22, abstention 11/11, safety 16/16 and scenarios 3/3 (`results/EVAL_REPORT.md`). FP16 answered 16 of 22 answerable questions, AWQ 13. Retrieval is not the bottleneck: every failure came from generation. And 15 of the 16 failed items were the model abstaining when the documents did contain the answer. That is the safe direction: the validator rejects unsupported numbers, so an uncertain model gives up instead of inventing. Latency p50 / p95 for questions reaching the model was 3.5 s / 6.7 s for FP16 and 1.5 s / 2.4 s for AWQ.

### 5.3 What does "abstention" mean here, and why is it treated as a feature? [intermediate]

The system answers "I don't have enough information in my documents" instead of guessing. It happens when nothing passes retrieval and grading (RET-01), when the model replies with the `INSUFFICIENT_CONTEXT` marker, or when the validator rejects the answer (`services/agent/src/fxassist_agent/graph.py`). In a regulated domain, a wrong leverage limit is worse than no answer. The cost is recall: FP16 abstained on 6 answerable questions and AWQ on 9. Abstentions are cached for only 2 minutes, because the next ingest may add the answer (CAC-03). The metric to watch in production is the fallback rate on the dashboard.

### 5.4 How do citation validation and number checks work, and what can they not catch? [advanced]

Code checks the answer against the excerpts the model was given (`services/agent/src/fxassist_agent/citations.py`). Citations to labels that were not provided are removed; an answer with no valid citation is rejected (RET-08). Every number in the answer must appear in one of the excerpts it cites, or the answer is rejected (SAF-03). Sentences after the last citation in a paragraph are dropped, because they are claims without a source, and injected text tends to land there. What it cannot catch: a real number attributed to the wrong thing. The local 3B model said "ESMA sets 20:1 for major pairs" when 20:1 is the limit for non-major pairs in the same excerpt (local, `LEARNING.md`). Catching that needs a stronger model or a per-claim check.

### 5.5 Tell me about the garbled-citation gap. [advanced]

AWQ answered one question with: "[ES2] ESMA sets a leverage limit of [S1] for retail clients on major currency pairs." (`results/EVAL_REPORT.md`). The model put a citation label where the number should be and invented a label, `[ES2]`. `[S1]` was a real excerpt, and the sentence had no number to check, so the validator kept it; only the content check (no "30:1") failed it. The normaliser deliberately does not guess what a garbled label like `[ES2]` meant. The documented fix is to treat a citation directly after words like "of", "is" or "at" as a missing value and reject the answer. Lesson: every output check has a blind spot, and a real model finds it.

### 5.6 The first GPU evaluation was invalid. What happened, and what did you change? [intermediate]

The first full GPU run scored FP16 39/52 and AWQ 38/52, with retrieval only 15/22 (`results/EVAL_REPORT.md`). All 10 Wikipedia documents were missing from the index, because Wikimedia rejected the downloader's generic User-Agent. Nothing crashed; it only showed up as a lower score. The run also used an older citation normaliser that rejected formats vLLM produced, such as `(S1)`. Both were fixed (a User-Agent with a contact URL, normalisation of unambiguous variants), the evaluation was rerun, and the first run is documented as not a result. Now the fetch and ingest reports travel with every evaluation, so an incomplete corpus is visible. A silent partial failure is worse than a crash.

### 5.7 Why no LLM-as-judge, and when would you use one? [advanced]

An LLM judge scores answers for qualities that string checks miss: completeness, faithfulness, tone. Its problems: cost (ADR-002 forbids paid APIs), its own errors and biases (preferring longer answers or its own style), and drift when the judge model changes. Here every check is deterministic and string-based, which is cheap, repeatable and honest about what it measures, but cannot judge fluency (`results/EVAL_REPORT.md`, Cautions). With a budget I would add a judge for faithfulness per claim, calibrate it against a few dozen human-labelled answers, pin the judge model and prompt, and keep the deterministic checks as a floor. Small samples remain the bigger limit: with 22 answerable questions, one item moves a rate by about 4.5 points, and one run per model cannot separate noise from real differences.

---

## 6. Safety and guardrails

### 6.1 How do you defend a RAG system against prompt injection? [intermediate]

In layers, cheapest first (`LEARNING.md`, Phase 1). Layer 1: rules in code catch advice requests, prompt-extraction and obvious injections before any model call (`services/agent/src/fxassist_agent/guards.py`). Layer 2: the prompt treats the question and the excerpts as untrusted data inside tags, and any copy of those tags inside the data is stripped first, so a document cannot close its own block (`prompts.py`). Layer 3: code checks the output: valid citations, numbers backed by the cited text, nothing after the last citation. Layer 3 caught two attacks the first two missed (eval items i09 and c02, local). Results: 10 of 10 injection attacks held with both GPU models (`results/EVAL_REPORT.md`).

### 6.2 Why treat retrieved text as untrusted? [basic]

Anyone who can get text into your corpus can put instructions in it: a web page, a PDF, a support ticket. The model cannot reliably tell your instructions from instructions inside the data. This is indirect prompt injection. The system prompt says excerpts are data and must never be followed (rule 1 in `prompts.py`), the delimiters cannot be forged, and the output checks remove the effects that slip through. The planted-excerpt scenario c02 puts an instruction inside a retrieved excerpt; it passed with both GPU models (RET-03, `results/EVAL_REPORT.md`).

### 6.3 How does the system refuse personal trading advice? [basic]

By code first, not by hoping the model refuses. The guard flags advice requests ("should I buy EUR/USD now?") and the agent answers with a fixed refusal plus a cited summary of the documents' general risk warnings, using a separate prompt addendum that forbids recommendations (SAF-01, SAF-02). Every answer also gets an "informational only" disclaimer added by the gateway, not by the model, so it is always there (SAF-06). All 4 advice items passed with both GPU models (`results/EVAL_REPORT.md`). Deterministic refusals are testable; a model's refusals vary with the model.

### 6.4 How are secrets kept out of git, images, logs and traces? [intermediate]

Git: gitleaks as a pre-commit hook, plus a full-history scan in CI, because the hook only sees staged changes (SAF-07). Kubernetes: values files hold no secrets; the chart reads a Secret by name, created by a script from the local `.env`, and a CI grep fails on secret-looking values (K8S-06). API keys are stored only as SHA-256 hashes (ADR-025). Logs and spans store a question hash and length, not the text; content on spans is opt-in, truncated and redacted (OBS-02). In the notebook, the Hugging Face token comes from Kaggle secrets and is never printed (GPU-11). If a secret is ever pushed, rotate it first, then clean history.

### 6.5 How are API keys checked, and what is the trade-off of ADR-025? [advanced]

Keys look like `fxa_<id>_<secret>`. Only the id and a SHA-256 of the whole key are stored. A fast hash is fine because keys are 256-bit random values; slow hashes like bcrypt protect guessable human passwords. Comparison uses `hmac.compare_digest`, and unknown ids are compared against a dummy hash, so timing does not reveal which ids exist (API-01). The gateway checks keys against an in-memory snapshot of the key table refreshed every 30 s, so PostgreSQL is not on the request path and an outage does not block requests (DEP-02). The cost: new or revoked keys take up to 30 s per replica, and the gateway is not ready until its first load. ADR-025 is still "proposed, awaiting approval" (`ARCHITECTURE.md`).

---

## 7. API gateway and reliability

### 7.1 What does the stream carry, and why not raw tokens? (ADR-024) [advanced]

The SSE stream sends `meta`, then one `status` event per graph node ("searching", "grading", "writing", "checking"), then one `answer` event with the validated text, citations and disclaimer, then `done`, or `error` (`ARCHITECTURE.md`, ADR-024). The validator needs the whole answer: it removes uncited sentences and rejects unsupported numbers. Streaming tokens before validation would show users exactly what the validator later removes, such as an injected sentence or an invented leverage limit. The cost is that users wait for the full generation before reading, but they see progress at once. The adapter still streams from the model internally, which gives honest TTFT, cancellation and stream-break detection. ADR-024 is implemented but still "proposed, awaiting approval"; token streaming could be added behind a flag for use cases that accept unvalidated drafts.

### 7.2 What are the practical problems of SSE through proxies, and what did you test? [intermediate]

Proxies and load balancers may buffer responses, so events arrive all at once at the end, and idle connections get closed. The gateway sets `X-Accel-Buffering: no` and `Cache-Control: no-cache, no-transform`, sends keep-alive comments every 10 s, and drops a client that stops reading for 15 s, so slow clients cannot pin memory (API-05, API-07, `services/gateway/src/fxassist_gateway/sse.py`). A test found that the response inherited `Content-Length: 0`, which breaks streaming. On kind, through nginx with default buffering, the first progress event arrived after 0.05 to 0.15 s (local, `EDGE_CASES.md` API-05). Honest detail: the stream worked even with the buffering header ignored, so on that proxy the header was not what made it work.

### 7.3 How does rate limiting work, and what happens when Redis is down? [intermediate]

A token bucket per API key, in Redis, so all replicas share one limit. It runs as a Lua script that reads Redis's own `TIME`, so gateway clock skew does not matter (CAC-06, `services/gateway/src/fxassist_gateway/redis_state.py`). Over the limit: 429 with `Retry-After` (API-02). If Redis is down, the limiter falls back to a per-process in-memory bucket 4x stricter, so an outage never means unlimited traffic; with N replicas the total is still bounded (CAC-01, `docs/runbooks/redis-down.md`). After a Redis restart the buckets are full again, so users may get one extra burst: allowing slightly more beats locking everyone out.

### 7.4 How do you design a cache for LLM answers so it never serves a stale answer? [intermediate]

Put everything the answer depends on into the key. Here it is SHA-256 of the normalised question, the corpus version, the prompt version and the model id (`services/gateway/src/fxassist_gateway/answers.py`). The prompt version is a hash of the prompt text, so nobody has to remember to bump it. Re-ingesting, editing a prompt or switching models changes every key, and old entries expire (CAC-02). Errors are never cached, abstentions only for 2 minutes, answers for 1 hour (CAC-03). The question enters only through the hash, so a caller cannot shape another entry's key (CAC-04). The cache fails open when Redis is down, and benchmarks refuse to run with it on (CAC-05). Semantic caching was deliberately out of scope.

### 7.5 What is request coalescing, and what is its limit here? [intermediate]

Without it, a popular question arriving 50 times during one generation costs 50 generations, because the cache only fills at the end (a cache stampede). With coalescing, the first request starts the run and identical ones (same cache key) wait for its result (API-04, `services/gateway/src/fxassist_gateway/runner.py`). A test sends 5 identical concurrent requests and sees 1 generation. The run belongs to the group, so the first client hanging up does not fail the others; it is cancelled only when everyone has left (LLM-05). The limit: coalescing is per gateway process. Across replicas the cache catches repeats after the first answer; a distributed lock in Redis would be the next step.

### 7.6 Explain timeouts, retries with jitter and the circuit breaker as a set. [intermediate]

A timeout bounds how long one call can hang: here 30 s of silence or 60 s total per model call (LLM-01). Retries hide short blips, but only for errors that are safe to repeat (connection refused, 429, 503), at most 2 times, with exponential backoff and full jitter so many clients do not retry in lockstep, honouring `Retry-After` (LLM-04). Timeouts and half-finished streams are deliberately not retried: that doubles load at the worst moment. The circuit breaker opens after 5 consecutive failures, fails fast with 503 for 30 s, then lets one trial call through (half-open) (`services/agent/src/fxassist_agent/config.py`). In the hanging-model drill, the breaker opened after about 1 minute, and closed 21 s after the model healed, through the half-open trial, with no restart (local, `docs/runbooks/llm-timeout-storm.md`).

### 7.7 What happens to a long generation when the user closes the browser? [advanced]

The SSE response notices the disconnect and leaves the request group. If nobody else is waiting for the same answer, the run is cancelled (LLM-05). The agent runs in a thread, and threads cannot be killed from outside, so cancellation is cooperative: a flag checked between graph nodes and between streamed chunks. When the adapter closes the HTTP connection to the model server, the server stops generating, which frees GPU capacity. The thread slot is released only when the thread really ends, so the concurrency limit stays honest. Known limit: a cancel is noticed at the next chunk, so a model still reading a long prompt finishes prefill first (`EDGE_CASES.md`, LLM-05).

### 7.8 How does graceful shutdown work, and why did the first rollout test still drop a request? [advanced]

Three parts: stop accepting new connections, let in-flight requests finish within a grace period, and report not-ready early so the load balancer stops sending traffic (API-08). On Kubernetes add a `preStop` sleep (5 s) so kube-proxy removes the pod first, and a termination grace period (45 s) longer than the sleep plus the app's 30 s drain (`deploy/helm/fxassist/values.yaml`). The first rollout test still failed 1 request in 17: draining answered 503 to requests arriving on an already-open keep-alive connection. Serving them with `Connection: close`, so the client reconnects to another pod, fixed it: 0 failed requests in 3 consecutive runs (local kind, K8S-07, `LEARNING.md` Phase 4).

### 7.9 Every dependency has a written failure policy. Give the policies and the reasoning. [intermediate]

Decide them before the failure. Redis down: the cache fails open (it is an optimisation) and the limiter falls back to a stricter local limit (never "no limit"); `/readyz` reports degraded, not failed (CAC-01). PostgreSQL down: keys come from the memory snapshot, request-log rows wait in a bounded buffer and are dropped with a metric when it fills (DEP-02). Qdrant down: 503, and the model is never called, because answering without documents would be making things up (DEP-01). Tracing backend down: spans are dropped, requests are unaffected (DEP-03). `make drill` stopped each store for real; all three recovered in under 5 s without a gateway restart (local, `LEARNING.md` Phase 2; `docs/runbooks/qdrant-down.md`, `redis-down.md`).

---

## 8. Observability

### 8.1 Metrics, traces and logs: what is each for? [basic]

Metrics are numbers over time, cheap to keep and to alert on: "how many timeouts per second?" Traces follow one request through every step: "where did this slow request spend its time?" Logs are the detailed record: "what exactly failed for request 3f2a?" (`LEARNING.md`, Phase 3). Here the gateway uses the OpenTelemetry API for metrics and spans; Prometheus scrapes metrics and Grafana shows them; traces can go to Langfuse or any OTLP endpoint. The request ID links logs to the request-log row; the trace ID links spans across services, and the adapter sends a W3C `traceparent` header to the model server. Honest limit: Langfuse export was tested only against fake endpoints.

### 8.2 What is metric cardinality and how did you control it? [intermediate]

Each distinct combination of label values is a separate time series held in Prometheus's memory. A label like `user_id` or `question` can create millions of series and take Prometheus down. Here labels come from fixed lists (route, status, outcome, error code, stage), and a test runs real traffic and checks every attribute against an allowlist; a second test checks every call site with an AST scan (OBS-01). Per-user and per-request detail belongs in logs and traces. Metrics also live on a separate port (9464) that is never published outside the compose network or cluster (OBS-04).

### 8.3 How do you measure TTFT correctly in a RAG service? [intermediate]

Measure it where the model call happens: from sending the generation request to the first content token, with streaming on. If you measure from the HTTP request, TTFT includes queue time and retrieval, and you blame the model for Qdrant. Here queue, retrieval, TTFT and total model time are separate histogram stages (OBS-05). A test injects a known 0.4 s first-token delay in the mock and checks only TTFT moves. Note the difference from the benchmark harness, whose TTFT deliberately includes queueing inside vLLM, because that is what the server delivers under load (`docs/BENCHMARK_METHOD.md`).

### 8.4 Your model died and no alert fired. How can that happen? [advanced]

That happened in a drill (local, `docs/runbooks/llm-timeout-storm.md`). The mock was set to accept requests and never answer, under mixed load, for 5 minutes. The first rules ("circuit open for 1 minute", "timeouts above 3 per minute", "5xx above 5% of requests") fired nothing. The cache kept answering popular questions, so 5xx were only about 2.5% of requests. The open breaker turned slow timeouts into fast `llm_circuit_open` errors, and its half-open trial every 30 s reset "open for 1 minute". The resilience layer hid the failure from user-facing metrics. The fix was to alert on the dependency: `FxaLLMFailing` (more than half of model calls fail for 2 minutes) fired at about 2 min 15 s, and `FxaLLMCircuitNotClosing` at about 3 min (`observability/prometheus/rules/fxassist.yml`). The general lesson: drill your alerts like any other code.

### 8.5 What SLOs would you set for this service? [advanced]

This project defined alerts, not formal SLOs, so this is how I would do it. Availability: share of valid requests that end in a non-5xx answer, say 99.5% over 30 days, with abstentions counted as successes but tracked separately. Latency: p95 of uncached answer time below a target taken from measurements; the GPU-lab eval's FP16 p95 was 6.7 s, so the target depends on model and hardware choice. Quality: fallback rate and eval pass rate per release, because a fast wrong answer is a failure too. Then alert on error-budget burn rate over two windows (fast and slow) rather than on raw thresholds. The existing `FxaSlowAnswers` and `FxaHighErrorRate` rules are the starting point.

### 8.6 Which vLLM metrics would you put on a dashboard and alert on? [intermediate]

`vllm:num_requests_running` and `vllm:num_requests_waiting` (queue), `vllm:kv_cache_usage_perc` (memory pressure), `vllm:num_preemptions_total` (the cache actually ran out), TTFT and inter-token latency histograms, and prompt and generation token rates. Prefix-cache hit counters if prefix caching is on. Alert on preemptions increasing and on KV usage above 0.9 for minutes, not on waiting requests alone, because waiting can be normal queueing (`docs/runbooks/kv-cache-exhaustion.md`). In the GPU lab these were sampled every 10 s into files, not scraped by Prometheus, because the notebook was not reachable (ADR-004, ADR-012).

---

## 9. Kubernetes and deployment

### 9.1 What are startup, readiness and liveness probes for? What does each check here? [basic]

Startup: has it finished starting? Liveness waits until it passes, which protects slow starts. Readiness: should it get traffic now? It includes dependencies, so a pod that lost Qdrant leaves the Service. Liveness: is the process stuck and should it be restarted? It checks the process only, never dependencies, or a database blip would restart every pod. Here startup and liveness use `/healthz` and readiness uses `/readyz`, with a 180 s startup budget (`deploy/helm/README.md`). On kind, a 60 s artificial slow start survived the 180 s budget with 0 restarts, and was killed twice in 150 s with a 30 s budget (local, K8S-01).

### 9.2 How do you roll out a new version with zero dropped requests? [intermediate]

Four pieces together: `maxUnavailable: 0` with `maxSurge: 1` (start a new pod before stopping an old one), a readiness probe that reflects real readiness, a `preStop` sleep so endpoint removal propagates before SIGTERM, and an app that drains in-flight work within a grace period. Add a PodDisruptionBudget (`minAvailable: 1`) so voluntary disruptions such as node drains cannot take all pods at once. Then prove it: `make kind-rollout-test` sends traffic during `kubectl rollout restart` and counts failures. The last 3 consecutive runs had 0 failed requests, after fixing the keep-alive 503 bug (local, K8S-07).

### 9.3 Requests vs limits: how did you set them, and what happened on the first deploy? [intermediate]

A request is what the scheduler reserves; a limit is where the kernel steps in. CPU over its limit is throttled; memory over its limit is killed (OOMKilled, exit code 137). Limits came from measurements: the gateway uses about 349 MiB, so it requests 512 Mi and is limited at 1 Gi (`deploy/helm/fxassist/values.yaml`). The first deploy proved the point: the ingestion Job was OOMKilled three times at 1.5 GiB, because ONNX attention memory grows with batch size times sequence length squared. The fix addressed the cause (embedding batch 64 to 16, peak about 931 MiB), not a bigger limit (local kind, `docs/runbooks/pod-oomkilled.md`). The pod was gone, but the node's kernel log still showed the cgroup OOM kills.

### 9.4 Describe the watchdog. Why hysteresis, and why such narrow RBAC? [advanced]

A CronJob sends a canary prompt every minute to the model server. A failure is an error, an empty or malformed answer, no answer within 30 s, or an answer slower than 20 s (`docs/runbooks/watchdog-restart-loop.md`). It warns first, restarts only after 3 consecutive failures, then waits a 10-minute cooldown, and stops after 2 restarts per hour so a human must look. Without that hysteresis, a slow-loading or misconfigured model becomes a restart loop that never finishes loading. The decision is a pure function, so a test simulates a whole broken hour in milliseconds; state lives in an annotation on the watched Deployment because CronJob pods are fresh each run. Its Role allows only `get` and `patch` on one named Deployment, proved with 17 `kubectl auth can-i` checks (K8S-05). On kind it watches the mock LLM, not vLLM.

### 9.5 The gateway has an HPA on CPU. Is CPU the right signal? [advanced]

The chart scales the gateway from 2 to 4 replicas at 70% CPU (`deploy/helm/fxassist/values.yaml`). For this gateway CPU is a weak signal: most of a request is waiting on the model, so the gateway can be saturated (all 4 agent slots busy, requests queueing) with low CPU. Better signals are in-flight agent runs (`fxa_agent_runs_active`) or queue time, exposed through a custom-metrics adapter or KEDA. For the model server itself, scale on `vllm:num_requests_waiting` or KV-cache usage, and remember a new replica takes over a minute to start (section 1.8), so scale early. None of the custom-metric scaling was built here; the HPA was tested on kind with metrics-server only.

### 9.6 Kind has no GPU. How would you schedule vLLM on a real GPU cluster? [advanced]

This project did not do it, so this is the plan, not experience. Install the NVIDIA GPU Operator (driver, container toolkit, device plugin, DCGM exporter), and request GPUs with `nvidia.com/gpu: 1` in resource limits. Put GPU nodes in their own pool with a taint, and give the vLLM pods a matching toleration and node affinity so nothing else lands there. Cache model weights on a persistent volume or local NVMe and keep vLLM's compile cache across restarts, since a cold start measured 77 to 104 s on a T4. Size the startup probe from that measurement. For tensor parallelism keep all shards on one node with NVLink; consider MIG or time-slicing only for small models. Scale on queue depth, and keep the gateway, Qdrant and Redis on cheap CPU nodes.

### 9.7 How do you keep Helm values from drifting between environments? [intermediate]

One base `values.yaml` holds everything, with comments; each environment file overrides only what differs: kind 3 keys, CI 9 keys (K8S-09, `deploy/helm/README.md`). `make helm-check` renders the chart for each environment and prints the diff, and CI runs it, so "works on kind, broken in CI" differences are visible in review. Tests check the rendered manifests: limits on every container, non-root everywhere, probe budgets, rollout settings, the watchdog's RBAC, and no secrets in values (`tests/test_helm.py`). Ingestion is a plain Job per revision, not a Helm hook, because a hook would deadlock with `helm --wait` waiting on the gateway's readiness.

---

## 10. CI/CD and supply chain

### 10.1 What makes a CI pipeline safe for pull requests from forks? [intermediate]

No secrets needed at all, so a fork PR runs the same jobs. Use the `pull_request` trigger with a read-only token, never `pull_request_target`, which runs a stranger's code with write access and secrets (`.github/workflows/ci.yml`). Nothing calls a GPU, a real model or a paid API; everything uses the mock LLM (CI-05). A test enforces these rules on the workflow files (CI-02). Honest status: CI is green for pushes to main, but a real pull request from a fork has not been tried yet (`EDGE_CASES.md`, CI-02 TODO).

### 10.2 Why download tools with checksum checks instead of using marketplace actions? [intermediate]

A third-party action is code that runs inside your pipeline, and a tag can be moved to a malicious commit. Here Helm, kind, kubectl, gitleaks and Trivy are downloaded from official releases and checked against published SHA-256 sums, at versions pinned in the workflow and recorded in `docs/VERSIONS.md` (`.github/workflows/ci.yml`). The remaining actions are `actions/checkout` and `astral-sh/setup-uv` with an exact tag. Pinning actions to a full commit SHA would be stronger still. One real lesson: actionlint passed, but the first run failed because `setup-uv@v10` did not exist; that action publishes only exact tags (local, `LEARNING.md`).

### 10.3 How do lockfiles give you reproducibility, and how do you catch drift? [basic]

`pyproject.toml` says what you want; `uv.lock` records the exact version and hash of every package, including transitive ones. CI and Docker install with `uv sync --locked`, so every machine gets the same packages (ADR-021). `uv lock --check` fails CI if someone changed dependencies without re-locking (CI-03). A weekly workflow (`.github/workflows/dependencies.yml`) reports what `uv lock --upgrade` would change without changing it, so updates become reviewed changes. For fast-moving libraries like vLLM and LangGraph, an unpinned install can change behaviour between two runs of the same commit.

### 10.4 How do you keep images small and safe, and why is the Trivy scan not blocking? [intermediate]

Multi-stage builds, non-root users, and pip, setuptools and wheel removed from the runtime stage. A size budget per image is checked in CI: the gateway was 233 MB against a 270 MB budget on GitHub, then 276 MB against 320 MB once Tesseract OCR was added for the web page (CI-04, ADR-027, `docs/VERSIONS.md`). The measure had to be redefined: Docker Desktop reported compressed sizes and GitHub's runners uncompressed ones, so the budget now uses the gzip size of `docker save`, which both agree on. The first Trivy scan found 2 HIGH CVEs in the base image's setuptools and wheel, which nothing used at runtime; removing them gave 0 fixable HIGH or CRITICAL (SAF-08). The scan reports rather than blocks, so a new base-image CVE becomes a reviewed version bump instead of a red build on an unrelated change. In production I would block on CRITICAL with a fix available.

### 10.5 What does the kind integration job prove, and what does it not? [intermediate]

It creates a kind cluster on a GitHub runner, builds and loads the images, deploys the Helm chart with CI overrides, runs the real ingestion Job (downloading the public documents), asks a question through the gateway and asserts the outcome is `answered`, then runs the RBAC check (`.github/workflows/ci.yml`). It proves the chart, images, probes, ingestion and the whole request path work together on real Kubernetes. It does not prove GPU behaviour, real model quality or cloud networking: the LLM is the mock. On failure it dumps pods, events and logs, so a red build is debuggable without a rerun.

---

## 11. Cost and capacity planning

### 11.1 How do you estimate whether a model and workload fit on a GPU? [intermediate]

Weights plus KV cache plus overhead, all under `gpu_memory_utilization`. Weights: parameters times bytes per parameter, or the safetensors size. KV cache: 36 KiB per token for this model, times the tokens in flight. Overhead: activations and CUDA graphs, about 0.7 GiB plus 0.1 GiB of graphs here (`docs/runbooks/gpu-oom.md`). The written estimate was about 6.7 GiB of KV cache and about 195,000 tokens; vLLM reported 6.86 GiB and 199,744 tokens (`docs/INTERVIEW_NOTES.md`, `results/BENCHMARKS.md`). Then confirm with the server's start-up log, and load-test, because memory fit says nothing about latency.

### 11.2 When would you quantise? [intermediate]

When decode dominates (short prompts, interactive chat, low to medium concurrency), when the model does not fit, or when the KV cache is the limit. Not when prompts are long and load is high, because prefill is compute bound and the speed gain shrinks to almost nothing (1.07x to 1.14x here). And always check quality on your own evaluation: AWQ passed 42/52 against FP16's 46/52, answering 3 fewer answerable questions (`results/EVAL_REPORT.md`). At this sample size that is a direction, not a precise measurement. The decision is a trade between cost per token, latency, memory and answer quality, measured on your workload.

### 11.3 How many users could one T4 serve for this workload? [advanced]

Work it out from measurements, with the caveats. On RAG-sized prompts, FP16 delivered 135 output tokens/s at 32 users with fixed 128-token answers, which is about 1 request per second per T4 (derived from `results/BENCHMARKS.md`). But at that load the p50 latency was 30 s and the p95 TTFT 17 s, which no chat user accepts. At 4 users latency p50 was about 7 s and throughput 72 tokens/s, about 0.6 requests per second (derived). So capacity is "requests per second at an acceptable p95", not peak throughput. Real answers are often longer than 128 tokens, and the answer cache (89% hit ratio in the local load test, with a repeating question pool) changes everything: estimate the real hit rate before buying GPUs.

### 11.4 What did the $0 constraint force, and what did it teach? [basic]

No EKS, no cloud GPUs, no paid APIs, no paid judge model (ADR-002). GPU work moved to a free Kaggle notebook, which forced a batch-job design: results written and fsynced row by row, resumable stages, an hour budget with priorities, and a dry run of the whole notebook on the laptop against the mock (GPU-05, GPU-06, `docs/KAGGLE_PLAYBOOK.md`). Kubernetes moved to kind without a GPU, which is why the watchdog restarts a mock. Evaluation is deterministic and string-based. The honest consequence: GPU-on-Kubernetes is "not proven" and stated as such. The useful habit it built: every GPU hour was planned, and everything that did not need a GPU was verified before one was used.

---

## 12. System design follow-ups

### 12.1 Scale this to 1,000 users. What changes? [advanced]

Start from load, not users: requests per second at peak, prompt and answer lengths, cache hit rate, and a p95 latency target. Measure one replica's capacity at that target (section 11.3), then divide; add headroom for a replica failing and for the 1 to 2 minutes a new vLLM replica takes to start. Gateways are stateless and scale horizontally already; rate limits and cache are in Redis, so they are shared. Request coalescing is per process, so add a distributed lock if stampedes matter. Model servers: replicas behind a load balancer that is aware of queue depth or prefix cache, autoscaled on `vllm:num_requests_waiting`. Qdrant: replicas for availability (a corpus of 727 chunks is tiny). PostgreSQL: managed, with retention on the request log. None of this was built; the current design keeps the boundaries (one OpenAI-compatible URL, stateless gateway) that make it possible.

### 12.2 Make it multi-tenant. [advanced]

Decide what is shared and what is isolated. Tenancy fits on the API key: a key belongs to a tenant, and the tenant sets rate limits, quotas (tokens per day, not just requests) and which corpus it may search. Retrieval must filter by tenant on every query: a separate Qdrant collection per tenant for strong isolation, or a payload filter on a shared collection for scale; a missing filter is a data leak, so test it. The cache key must include the tenant or corpus id, because today it deliberately excludes the caller (`answers.py`). Metrics must not gain a `tenant` label if tenants are unbounded; use logs, traces or a bounded tier label. Fair scheduling on the model server (priorities or per-tenant concurrency caps) stops one tenant from filling the queue.

### 12.3 Swap in a bigger model. What breaks, and in what order do you check? [advanced]

Memory first: recompute weights and KV per token from the new `config.json`. A 7B AWQ model (5.57 GB according to `docs/KAGGLE_PLAYBOOK.md`) leaves much less KV cache on a T4 than the 3B model did. Then dtype support, attention backend and quantisation kernels on the target GPU; verify against the vLLM version before spending GPU hours, as in section 1.9. Then start-up time and probes, then the benchmark matrix with the same method, then the full evaluation. The OpenAI-compatible interface means the gateway does not change (ADR-003), and the model id is in the cache key, so old answers are never served (CAC-02). Check the prompt too: a different model may cite or abstain differently, and the validator was tuned on real model output.

### 12.4 You fine-tuned the model. How, and what happened? [advanced]

ADR-026 added one LoRA experiment. Data: a larger open model (Qwen2.5-7B-Instruct-AWQ) answered 328 questions written from the corpus, **through the real agent**, so training prompts match inference exactly; only answers that passed the agent's citation and number checks were kept (266), plus grader examples and 39 "these excerpts do not answer this" examples. 98 of 438 generated questions were dropped for being within cosine 0.80 of an evaluation question: without that filter, a quarter of the training set would have overlapped the test. Training: LoRA rank 16 on all projections (0.96% of parameters), float16 base with float32 adapter weights and loss scaling because a T4 has no bfloat16, 69 steps in 18 minutes, validation loss 0.454 to 0.228 (`results/FINETUNE_REPORT.md`). Result: the same 46/52 as the base model, evaluated in the same session. Abstentions on answerable questions fell from 6 to 0 with three new correct answers, but five answers were just `[S1]`, and the model followed an instruction planted in an excerpt that the base model resisted. Causes: 12 of the 266 "validated" teacher answers were bare citations the validator accepted (a production bug too, now RET-11), and no training example taught it to ignore planted instructions (FT-05, now added). Decision: not promoted, because the gate is "no safety regression", not "a higher score". The lessons: your filter is your label quality, and fine-tuning can silently remove behaviour the base model had, so the safety set is a release gate.

### 12.5 Add image input (scanned documents or screenshots). [intermediate]

Two different problems. Images in user questions are built here, the simple way (ADR-027): the web page uploads a screenshot to `POST /v1/ocr`, where the gateway checks it (5 MB cap while reading the body, declared type must match the bytes, pixel count checked before decoding, so no decompression bombs), re-encodes it as grayscale PNG and runs Tesseract in a subprocess with a timeout, at most two at once (API-09). The text is shown to the user for editing and sent as part of the question, so the injection guard and the number check apply unchanged; an instruction hidden in an image is refused like a typed one (SAF-09). The trade-off: numbers in a screenshot are never accepted as cited facts, so with a 3B model, screenshot questions are often answered cautiously. The alternative is a vision-language model behind the same OpenAI-compatible interface: it can read charts, but images become many prompt tokens (longer prefill, section 2.2), its reading of numbers cannot be checked, and charts invite price-prediction questions the system must refuse. Images in the corpus (scanned PDFs, skipped today, DAT-01) are an ingestion problem: OCR at ingestion time, the page image kept for citations, and extraction quality measured on a sample, because OCR errors in numbers are exactly what the number check exists to catch.

### 12.6 What would you change first to make this production-ready? [intermediate]

Real secrets management (External Secrets or a vault) instead of a script. Alertmanager routing, plus kube-state-metrics so the documented OOM alerts actually run. Replicated Qdrant, a managed PostgreSQL with retention on the request log, and backups for both. Autoscaling on queue depth, GPU nodes with the model server behind the same interface, and a load test that sets the SLOs. A real Langfuse or OTLP backend, tried for real. Approval of ADR-024 and ADR-025, and token-level streaming behind a flag if a use case accepts unvalidated drafts (`docs/INTERVIEW_NOTES.md`). And a fork pull request through CI, to close CI-02.
