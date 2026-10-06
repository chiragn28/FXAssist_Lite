# agent

The RAG core (Phase 1): ingestion, retrieval and the LangGraph agent. Python package `fxassist_agent`, command `fxassist` (wrapped by `make fetch | ingest | ask | eval | experiment`).

## Flow

```
guard ──(vague / injection / reveal)──> fixed response, no model call
  │ (advice: decline in code, then summarise documented risk warnings)
  ▼
retrieve ──(best score below out-of-scope threshold)──> "outside what I can help with"
  │   search top_k x 4, drop near-duplicates (RET-10), max 2 excerpts per document
  ▼
grade ──(LLM picks relevant excerpts; malformed JSON = none, RET-07)
  │   nothing relevant: rewrite the query once, retrieve again; still nothing: abstain (RET-01)
  ▼
generate ──(excerpts trimmed to fit 4096 tokens, RET-06; "insufficient context" = abstain)
  ▼
validate ──(unknown citations removed, RET-08; numbers must appear in cited text, SAF-03;
            claims after the last citation dropped, RET-03/SAF-04)
```

Caps on every request: 12 graph steps and 120 seconds (RET-09).

## Modules

| Module | Job |
|---|---|
| `config.py` | `FXA_*` settings with safe defaults |
| `sources.py` | `data/sources.yaml` registry; renders `data/SOURCES.md` |
| `fetch.py` | Downloads with atomic writes, retries and a report (DAT-07) |
| `extract.py` | PDF/HTML/Wikipedia to clean page text; boilerplate and language checks (DAT-04/05/06) |
| `chunking.py` | Stable content-hash chunk IDs, streaming, cap (DAT-02/04) |
| `embeddings.py` | fastembed bge-small (ADR-023); `HashEmbedder` for offline tests |
| `store.py` | Qdrant collection with model metadata and corpus version (DAT-03/10) |
| `ingest.py` | The ingestion pipeline and its report |
| `llm.py` | Minimal OpenAI-compatible client with a model check (ENV-03) |
| `guards.py`, `prompts.py`, `citations.py` | The three layers of safety |
| `graph.py` | The LangGraph agent |
| `evaluate.py` | `make eval` and `make experiment` |

## Tests

`services/agent/tests` runs offline: in-memory Qdrant, a hash-based fake embedder and a scripted fake LLM. Real-model behaviour is measured by `make eval`.
