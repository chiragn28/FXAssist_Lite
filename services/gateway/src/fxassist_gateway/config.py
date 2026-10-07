"""Gateway settings: everything the agent needs, plus the API, cache, limiter and database.

Read from FXA_* environment variables and .env, like the agent's settings.
"""

from __future__ import annotations

from pydantic import SecretStr

from fxassist_agent.config import Settings

# SAF-06: added by the gateway to every response, never left to the model.
DISCLAIMER = (
    "General information from public documents, not financial advice. Trading forex and CFDs "
    "carries a high risk of losing money quickly because of leverage."
)


class GatewaySettings(Settings):
    gateway_port: int = 8000

    # --- Redis: cache and rate limit (ADR-010) ---
    redis_port: int = 6379
    redis_url: str | None = None
    redis_timeout_s: float = 0.25  # an outage must not add latency to every request (CAC-01)
    redis_retry_after_s: float = 5.0  # after a Redis failure, skip it for this long

    # --- PostgreSQL: API keys and request log (ADR-011) ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "fxassist"
    postgres_db: str = "fxassist"
    postgres_password: SecretStr = SecretStr("")
    key_refresh_s: float = 30.0  # revoked keys stop working within this time
    log_queue_max: int = 1000  # DEP-02: bounded buffer; beyond it, entries are dropped and counted
    log_batch_size: int = 100
    log_questions: bool = False  # OBS-02: store a hash and length, not the text, by default

    # --- API (API-02, API-03) ---
    max_body_bytes: int = 16_384
    rate_limit_per_minute: float = 30.0
    rate_limit_burst: int = 10
    rate_limit_fallback_divisor: int = 4  # in-memory limit when Redis is down is 4x stricter

    # --- Web page and image text (ADR-027) ---
    ui_enabled: bool = True
    ocr_enabled: bool = True
    ocr_command: str = "tesseract"
    ocr_max_bytes: int = 5_000_000
    ocr_max_pixels: int = 20_000_000  # checked before decoding (no decompression bombs)
    ocr_max_chars: int = 900  # leaves room for the user's own words in a 1000-character question
    ocr_timeout_s: float = 20.0
    ocr_concurrency: int = 2  # OCR is CPU work in the gateway: at most this many at once

    # --- Cache (ADR-010, CAC-03) ---
    cache_enabled: bool = True
    cache_ttl_s: int = 3600
    cache_short_ttl_s: int = 120  # abstentions: the answer may appear after the next ingest

    # --- Concurrency and streaming (API-04, API-05, API-07) ---
    max_concurrent_runs: int = 4
    queue_timeout_s: float = 10.0
    sse_keepalive_s: float = 10.0
    sse_write_timeout_s: float = 15.0

    # --- Health and shutdown (ADR-014, API-08) ---
    ready_llm_cache_s: float = 15.0  # reuse the last LLM probe result for this long
    ready_timeout_s: float = 5.0
    index_info_ttl_s: float = 5.0  # corpus version is re-read at most this often
    shutdown_grace_s: float = 30.0
    debug_startup_delay_s: float = 0.0  # K8S-01 drill only: pretend startup is slow

    # --- Observability (ADR-012, Phase 3) ---
    metrics_host: str = "127.0.0.1"  # 0.0.0.0 inside containers; never published (OBS-04)
    metrics_port: int = 9464
    otlp_traces_endpoint: str | None = None  # any OTLP/HTTP traces URL
    otlp_headers: SecretStr = SecretStr("")  # "key=value,key2=value2"; may hold credentials
    langfuse_public_key: str = ""  # optional Langfuse Cloud free tier
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"
    trace_export_timeout_s: float = 2.0  # DEP-03: a slow tracing backend costs at most this

    @property
    def resolved_redis_url(self) -> str:
        return self.redis_url or f"redis://localhost:{self.redis_port}/0"

    @property
    def postgres_conninfo(self) -> str:
        from psycopg.conninfo import make_conninfo

        return make_conninfo(
            host=self.postgres_host,
            port=self.postgres_port,
            user=self.postgres_user,
            dbname=self.postgres_db,
            password=self.postgres_password.get_secret_value(),
            connect_timeout=3,
            application_name="fxassist-gateway",
        )

    @property
    def model_id(self) -> str:
        """The model as the cache sees it: name plus server, so a mock answer can never be
        served after switching to a real model with the same name (ADR-010, CAC-02)."""
        return f"{self.llm_model}@{self.resolved_llm_base_url}"
