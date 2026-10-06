"""Non-blocking request logging (ADR-011, DEP-02).

`submit()` never waits and never fails: it puts the entry in a bounded queue. A background task
writes batches to the database. While the database is down, the batch being written is kept
and retried with backoff, and new entries collect in the queue; once the queue is full, new
entries are dropped and counted. A logging outage can cost log rows, never user requests.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Protocol

from . import metrics

log = logging.getLogger(__name__)


class LogStore(Protocol):
    async def write_logs(self, entries: list[dict[str, Any]]) -> None: ...


class InMemoryLogStore:
    """For tests; `fail` simulates the database being down."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = False

    async def write_logs(self, entries: list[dict[str, Any]]) -> None:
        if self.fail:
            raise ConnectionError("log store unavailable")
        self.rows.extend(entries)


class RequestLogWriter:
    def __init__(
        self,
        store: LogStore,
        *,
        max_queue: int,
        batch_size: int,
        retry_base_s: float = 0.5,
        retry_max_s: float = 10.0,
    ):
        self.store = store
        self.batch_size = batch_size
        self.retry_base_s = retry_base_s
        self.retry_max_s = retry_max_s
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self.dropped = 0
        self.written = 0
        self.failing = False
        self._pending: list[dict[str, Any]] = []

    def submit(self, entry: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.dropped += 1
            metrics.log_entries_dropped.add(1)

    def _take_batch(self) -> list[dict[str, Any]]:
        batch = []
        while len(batch) < self.batch_size and not self.queue.empty():
            batch.append(self.queue.get_nowait())
        return batch

    async def _write_pending(self) -> bool:
        try:
            await self.store.write_logs(self._pending)
        except Exception as exc:  # any database failure
            if not self.failing:
                log.warning("request log write failed (%s); buffering", exc)
                metrics.dependency_failures.add(1, {"dependency": "postgres"})
            self.failing = True
            return False
        self.written += len(self._pending)
        self._pending = []
        if self.failing:
            log.info("request log writes recovered")
        self.failing = False
        return True

    async def run(self) -> None:
        delay = self.retry_base_s
        while True:
            if not self._pending:
                self._pending = [await self.queue.get()]
                self._pending += self._take_batch()
            if await self._write_pending():
                delay = self.retry_base_s
            else:
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.retry_max_s)

    async def flush(self, timeout_s: float) -> None:
        """At shutdown: one last attempt to write what is buffered."""

        async def drain() -> None:
            while self._pending or not self.queue.empty():
                if not self._pending:
                    self._pending = self._take_batch()
                if not await self._write_pending():
                    return

        with suppress(TimeoutError):
            await asyncio.wait_for(drain(), timeout_s)
        lost = len(self._pending) + self.queue.qsize()
        if lost:
            self.dropped += lost
            metrics.log_entries_dropped.add(lost)
            log.warning("dropped %d request log entries at shutdown", lost)
