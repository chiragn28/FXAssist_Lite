"""Settings for the agent, read from FXA_* environment variables and .env.

Every value has a safe default, so tests and `make ask` work without any configuration.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FXA_", env_file=".env", extra="ignore")

    # --- Paths (relative to the repo root, where make runs) ---
    data_dir: Path = Path("data")
    eval_dir: Path = Path("eval")

    # --- Qdrant (ADR-008). The URL is derived from the compose port unless set. ---
    qdrant_http_port: int = 6333
    qdrant_url: str | None = None
    collection: str = "fxassist_docs"

    # --- Embeddings (ADR-009, ADR-023) ---
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    embed_batch_size: int = 64

    # --- Chunking (DAT-09: defaults chosen by `make experiment`, see LEARNING.md) ---
    chunk_size: int = 1000
    chunk_overlap: int = 150
    min_chunk_chars: int = 80
    max_chunks_per_doc: int = 1500  # DAT-04

    # --- Retrieval (RET-01, RET-05, RET-10) ---
    top_k: int = 5
    score_threshold: float = 0.55  # below this a chunk is not relevant enough to use
    out_of_scope_threshold: float = 0.45  # below this for every chunk: off-domain question
    near_duplicate_similarity: float = 0.95
    max_per_source: int = 2  # 0 disables the per-document cap
    candidate_multiplier: int = 4  # search top_k * this, then dedupe and diversify

    # --- LLM (ADR-003): any OpenAI-compatible server ---
    ollama_port: int = 11434
    llm_base_url: str | None = None
    llm_model: str = "qwen2.5:3b-instruct"
    llm_api_key: SecretStr = SecretStr("not-needed")
    llm_timeout_s: float = 60.0  # total budget for one model call (LLM-01)
    llm_connect_timeout_s: float = 5.0
    llm_read_timeout_s: float = 30.0  # longest silence: before the first token or between tokens
    llm_max_retries: int = 2  # retries after the first attempt, for 429/5xx/connect (LLM-04)
    llm_backoff_base_s: float = 0.5
    llm_backoff_max_s: float = 4.0
    llm_breaker_failures: int = 5  # consecutive failures that open the circuit (LLM-04)
    llm_breaker_reset_s: float = 30.0  # how long it stays open before one trial call
    llm_max_context_tokens: int = 4096  # Ollama's default context window
    llm_max_output_tokens: int = 400
    grader_enabled: bool = True

    # --- Agent safety caps (RET-09) ---
    max_graph_steps: int = 12
    max_request_seconds: float = 120.0
    max_rewrites: int = 1
    max_question_chars: int = 1000

    @property
    def resolved_qdrant_url(self) -> str:
        return self.qdrant_url or f"http://localhost:{self.qdrant_http_port}"

    @property
    def resolved_llm_base_url(self) -> str:
        return self.llm_base_url or f"http://localhost:{self.ollama_port}/v1"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def sources_file(self) -> Path:
        return self.data_dir / "sources.yaml"
