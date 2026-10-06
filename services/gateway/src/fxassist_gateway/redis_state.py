"""Redis-backed state with the failure policy from ADR-010.

  - Answer cache: fail open. If Redis is down, the request is computed as if it were a miss.
  - Rate limiter: token bucket per API key, in Redis so all gateway replicas share it. If Redis
    is down, a per-process in-memory bucket takes over with a stricter limit, so an outage
    cannot mean unlimited traffic (CAC-01).
  - After a Redis failure, Redis is skipped for `retry_after_s`, so an outage costs one short
    timeout per interval instead of one per request.

Clock skew and restarts (CAC-06): the limiter reads the time from Redis itself (`TIME` inside
the Lua script), so gateway clocks do not matter. A Redis restart empties every bucket, which
refills them: clients may briefly get one extra burst. Erring towards allowing slightly more is
deliberate; locking everyone out would be worse.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, TypeVar

from . import metrics

log = logging.getLogger(__name__)
T = TypeVar("T")


class RedisSkipped(Exception):
    """Redis is (recently) unavailable; the caller applies its fallback."""


class RedisGuard:
    def __init__(self, client, retry_after_s: float, clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.retry_after_s = retry_after_s
        self._clock = clock
        self._down_until = 0.0
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return self._clock() >= self._down_until

    async def call(self, op: Callable[[], Awaitable[T]]) -> T:
        if not self.available:
            raise RedisSkipped(self.last_error or "redis unavailable")
        try:
            result = await op()
        except Exception as exc:  # redis-py raises ConnectionError, TimeoutError, OSError...
            self._down_until = self._clock() + self.retry_after_s
            self.last_error = type(exc).__name__
            metrics.dependency_failures.add(1, {"dependency": "redis"})
            log.warning("Redis failed (%s); skipping it for %.0fs", exc, self.retry_after_s)
            raise RedisSkipped(self.last_error) from exc
        self.last_error = None
        return result

    async def ping(self) -> bool:
        try:
            await self.call(self.client.ping)
            return True
        except RedisSkipped:
            return False


# --- Rate limiter (API-02) --------------------------------------------------------------------

# KEYS[1] = bucket key. ARGV = refill rate (tokens/s), capacity, cost.
# Returns {allowed (0/1), seconds until allowed (string), tokens left (string)}.
BUCKET_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local wait = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  wait = (cost - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 60)
return {allowed, tostring(wait), tostring(tokens)}
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: float
    backend: str  # "redis" or "memory"


class MemoryBucket:
    def __init__(self, rate: float, capacity: float, clock: Callable[[], float]):
        self.rate = rate
        self.capacity = capacity
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        now = self._clock()
        tokens, ts = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - ts) * self.rate)
        if tokens >= cost:
            self._buckets[key] = (tokens - cost, now)
            return True, 0.0
        self._buckets[key] = (tokens, now)
        return False, (cost - tokens) / self.rate


class RateLimiter:
    def __init__(
        self,
        guard: RedisGuard,
        *,
        per_minute: float,
        burst: int,
        fallback_divisor: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.guard = guard
        self.rate = per_minute / 60.0
        self.burst = burst
        self._script = guard.client.register_script(BUCKET_LUA)
        divisor = max(1, fallback_divisor)
        self.fallback = MemoryBucket(self.rate / divisor, max(1, math.ceil(burst / divisor)), clock)

    async def check(self, key_id: str) -> Decision:
        try:
            allowed, wait, _ = await self.guard.call(
                lambda: self._script(keys=[f"fxa:rl:{key_id}"], args=[self.rate, self.burst, 1])
            )
            decision = Decision(bool(int(allowed)), float(wait), "redis")
        except RedisSkipped:
            ok, wait = self.fallback.take(key_id)  # CAC-01: conservative, per process
            decision = Decision(ok, wait, "memory")
        metrics.rate_limit.add(
            1, {"backend": decision.backend, "allowed": str(decision.allowed).lower()}
        )
        return decision


# --- Answer cache (ADR-010, CAC-01..04) -------------------------------------------------------


class AnswerCache:
    def __init__(self, guard: RedisGuard, *, enabled: bool):
        self.guard = guard
        self.enabled = enabled

    async def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            metrics.cache_lookups.add(1, {"result": "disabled"})
            return None
        try:
            raw = await self.guard.call(lambda: self.guard.client.get(key))
        except RedisSkipped:
            metrics.cache_lookups.add(1, {"result": "skip"})  # CAC-01: fail open
            return None
        if raw is None:
            metrics.cache_lookups.add(1, {"result": "miss"})
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            log.warning("ignoring unreadable cache entry")
            metrics.cache_lookups.add(1, {"result": "miss"})
            return None
        metrics.cache_lookups.add(1, {"result": "hit"})
        return value

    async def set(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        if not self.enabled or ttl_s <= 0:
            return
        payload = json.dumps(value, separators=(",", ":"))
        with suppress(RedisSkipped):  # CAC-01: the answer is still returned, just not remembered
            await self.guard.call(lambda: self.guard.client.set(key, payload, ex=ttl_s))
