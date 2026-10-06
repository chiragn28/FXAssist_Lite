"""API keys (ADR-011, ADR-018, API-01).

Format: fxa_<key id: 8 hex>_<secret: 43 url-safe chars>. Only the key id and a SHA-256 hash of
the whole key are stored. A fast hash is fine here because keys are 256-bit random values:
there is nothing to brute-force, unlike human passwords, which need a slow hash.

Verification never touches the database. The gateway keeps an in-memory snapshot of the key
table, refreshed every `key_refresh_s`, so:
  - PostgreSQL being down does not fail requests (DEP-02); the last good snapshot is used
  - a revoked key stops working within one refresh interval
The presented key's hash is compared with `hmac.compare_digest` (constant time), and an unknown
key id is compared against a dummy hash, so timing does not reveal which key ids exist.
Every failure returns the same 401 message.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .errors import ApiError

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^fxa_([0-9a-f]{8})_[A-Za-z0-9_-]{43}$")
_DUMMY_HASH = hashlib.sha256(b"no such key").hexdigest()


def unauthorized() -> ApiError:
    # A new instance each time: re-raising one shared exception would keep growing its traceback.
    return ApiError(
        401, "unauthorized", "Missing or invalid API key.", headers={"WWW-Authenticate": "Bearer"}
    )


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Returns (full key to show once, key id, hash to store)."""
    key_id = secrets.token_hex(4)
    key = f"fxa_{key_id}_{secrets.token_urlsafe(32)}"
    return key, key_id, hash_key(key)


def parse_key_id(key: str) -> str | None:
    match = _KEY_RE.match(key)
    return match.group(1) if match else None


def presented_key(headers) -> str | None:
    """`Authorization: Bearer <key>` or `X-API-Key: <key>`."""
    auth = headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        return value.strip() if scheme.lower() == "bearer" else None
    return headers.get("x-api-key")


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    key_hash: str
    name: str
    revoked: bool = False


class KeyStore(Protocol):
    async def load_keys(self) -> list[KeyRecord]: ...

    async def add_key(self, record: KeyRecord) -> None: ...

    async def revoke_key(self, key_id: str) -> bool: ...


class InMemoryKeyStore:
    """For tests; `fail` simulates the database being down."""

    def __init__(self, records: list[KeyRecord] | None = None):
        self.records = {r.key_id: r for r in records or []}
        self.fail = False

    async def load_keys(self) -> list[KeyRecord]:
        if self.fail:
            raise ConnectionError("key store unavailable")
        return list(self.records.values())

    async def add_key(self, record: KeyRecord) -> None:
        self.records[record.key_id] = record

    async def revoke_key(self, key_id: str) -> bool:
        record = self.records.get(key_id)
        if record is None:
            return False
        self.records[key_id] = KeyRecord(record.key_id, record.key_hash, record.name, True)
        return True


class Authenticator:
    def __init__(
        self, store: KeyStore, refresh_s: float, clock: Callable[[], float] = time.monotonic
    ):
        self.store = store
        self.refresh_s = refresh_s
        self._clock = clock
        self._snapshot: dict[str, KeyRecord] | None = None
        self.loaded_at: float | None = None
        self.last_error: str | None = None

    @property
    def ready(self) -> bool:
        return self._snapshot is not None

    async def refresh(self) -> bool:
        try:
            records = await self.store.load_keys()
        except Exception as exc:  # any database failure: keep the last good snapshot
            self.last_error = type(exc).__name__
            log.warning("could not refresh API keys (%s); keeping the last snapshot", exc)
            return False
        self._snapshot = {r.key_id: r for r in records}
        self.loaded_at = self._clock()
        self.last_error = None
        return True

    async def run(self) -> None:
        """Refresh forever; retry quickly (with backoff) until the first load succeeds."""
        delay = 0.5
        while True:
            ok = await self.refresh()
            if ok:
                delay = 0.5
                await asyncio.sleep(self.refresh_s)
            else:
                await asyncio.sleep(delay if not self.ready else self.refresh_s)
                delay = min(delay * 2, self.refresh_s)

    def verify(self, key: str | None) -> KeyRecord:
        if self._snapshot is None:
            raise ApiError(503, "auth_unavailable", "Authentication is starting up.", retry_after=2)
        key = key or ""
        key_id = parse_key_id(key)
        record = self._snapshot.get(key_id) if key_id else None
        expected = record.key_hash if record else _DUMMY_HASH
        matches = hmac.compare_digest(hash_key(key), expected)
        if not (matches and record and not record.revoked):
            raise unauthorized()
        return record
