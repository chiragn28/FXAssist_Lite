"""PostgreSQL: API keys and the request log (ADR-011).

The pool opens in the background (`open(wait=False)`), so the gateway starts even when
PostgreSQL is not up yet (DEP-04) and keeps working while it is down (DEP-02): keys come from
the in-memory snapshot and log entries wait in a bounded buffer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from psycopg_pool import AsyncConnectionPool

from .auth import KeyRecord

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_id      text PRIMARY KEY,
    key_hash    text NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz
);

-- One row per API request. No question text by default (OBS-02): a hash and a length are
-- enough to spot repeats and abuse without storing what people asked.
CREATE TABLE IF NOT EXISTS request_log (
    id               bigserial PRIMARY KEY,
    ts               timestamptz NOT NULL,
    request_id       text NOT NULL,
    key_id           text,
    route            text NOT NULL,
    status           integer NOT NULL,
    outcome          text,
    error_code       text,
    cached           boolean NOT NULL DEFAULT false,
    coalesced        boolean NOT NULL DEFAULT false,
    streamed         boolean NOT NULL DEFAULT false,
    question_sha256  text,
    question_chars   integer,
    question_text    text,
    corpus_version   text,
    model            text,
    queue_ms         integer,
    retrieval_ms     integer,
    ttft_ms          integer,
    llm_ms           integer,
    total_ms         integer NOT NULL,
    completion_tokens integer,
    truncated        integer
);
CREATE INDEX IF NOT EXISTS request_log_ts ON request_log (ts);

-- Evaluation history: one row per `make eval` run that is recorded (ADR-011).
CREATE TABLE IF NOT EXISTS eval_runs (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    model       text NOT NULL,
    corpus_version text,
    summary     jsonb NOT NULL
);
"""

LOG_COLUMNS = (
    "ts",
    "request_id",
    "key_id",
    "route",
    "status",
    "outcome",
    "error_code",
    "cached",
    "coalesced",
    "streamed",
    "question_sha256",
    "question_chars",
    "question_text",
    "corpus_version",
    "model",
    "queue_ms",
    "retrieval_ms",
    "ttft_ms",
    "llm_ms",
    "total_ms",
    "completion_tokens",
    "truncated",
)


class PostgresStore:
    """KeyStore and LogStore on one connection pool."""

    def __init__(self, conninfo: str, *, max_size: int = 4):
        self.pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=max_size,
            open=False,
            timeout=2.0,  # waiting for a connection: fail fast, the callers have fallbacks
            reconnect_timeout=float("inf"),  # keep trying for as long as the gateway runs
        )
        self._schema_ready = False

    async def open(self) -> None:
        await self.pool.open(wait=False)

    async def close(self) -> None:
        await self.pool.close(timeout=5.0)

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA)
        self._schema_ready = True

    async def ping(self) -> bool:
        try:
            async with self.pool.connection(timeout=1.0) as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:  # any failure means "not reachable right now"
            return False

    # --- KeyStore ---------------------------------------------------------------------

    async def load_keys(self) -> list[KeyRecord]:
        await self.ensure_schema()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT key_id, key_hash, name, revoked_at IS NOT NULL FROM api_keys"
            )
            rows = await cur.fetchall()
        return [KeyRecord(r[0], r[1], r[2], bool(r[3])) for r in rows]

    async def add_key(self, record: KeyRecord) -> None:
        await self.ensure_schema()
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO api_keys (key_id, key_hash, name) VALUES (%s, %s, %s)",
                (record.key_id, record.key_hash, record.name),
            )

    async def revoke_key(self, key_id: str) -> bool:
        await self.ensure_schema()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE key_id = %s AND revoked_at IS NULL",
                (key_id,),
            )
            return cur.rowcount == 1

    async def list_keys(self) -> list[tuple[str, str, datetime, datetime | None]]:
        await self.ensure_schema()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT key_id, name, created_at, revoked_at FROM api_keys ORDER BY created_at"
            )
            return list(await cur.fetchall())

    # --- LogStore ---------------------------------------------------------------------

    async def write_logs(self, entries: list[dict[str, Any]]) -> None:
        await self.ensure_schema()
        placeholders = ", ".join(["%s"] * len(LOG_COLUMNS))
        sql = f"INSERT INTO request_log ({', '.join(LOG_COLUMNS)}) VALUES ({placeholders})"  # noqa: S608 - fixed column names
        rows = [tuple(e.get(c) for c in LOG_COLUMNS) for e in entries]
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(sql, rows)


def eval_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-category pass counts and the headline rates of one `fxassist eval` run."""
    by_category: dict[str, dict[str, int]] = {}
    for item in items:
        c = by_category.setdefault(item.get("category", "other"), {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += bool(item.get("passed"))
    retrieval = [i["retrieval_hit"] for i in items if i.get("retrieval_hit") is not None]
    citations = [i["citation_ok"] for i in items if i.get("citation_ok") is not None]
    return {
        "items": len(items),
        "passed": sum(bool(i.get("passed")) for i in items),
        "by_category": by_category,
        "retrieval_hit": {"hit": sum(retrieval), "of": len(retrieval)},
        "citation_ok": {"ok": sum(citations), "of": len(citations)},
    }


async def add_eval_run(store: PostgresStore, model: str, summary: dict[str, Any]) -> None:
    """ADR-011: evaluation history, one row per recorded `fxassist eval` run."""
    from psycopg.types.json import Jsonb

    await store.ensure_schema()
    async with store.pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eval_runs (model, summary) VALUES (%s, %s)", (model, Jsonb(summary))
        )


def now_utc() -> datetime:
    return datetime.now(UTC)
