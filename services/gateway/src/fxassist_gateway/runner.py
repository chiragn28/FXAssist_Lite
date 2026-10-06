"""Running the synchronous agent from the async gateway.

AgentRunner: the agent runs in a fixed-size thread pool. A semaphore with a timeout gives a
bounded queue: past `queue_timeout_s` the caller gets 503 "busy" instead of waiting forever.
The time spent waiting is recorded as queue time (OBS-05). A slot is released only when the
agent's thread has really finished, so the concurrency limit stays true even after a cancel.

Flights (request coalescing, API-04): identical requests (same cache key) arriving while one is
already being computed join that computation instead of starting another. The run belongs to
the flight, not to the first client, so the first client disconnecting does not fail the
others. The run is cancelled only when every waiting client has gone (LLM-05).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fxassist_agent.control import RunControl
from fxassist_agent.graph import Agent, AgentResult

from . import metrics
from .errors import ApiError

log = logging.getLogger(__name__)


@dataclass
class RunOutcome:
    result: AgentResult
    control: RunControl
    queue_s: float


class AgentRunner:
    def __init__(
        self,
        make_agent: Callable[[RunControl], Agent],
        *,
        max_concurrent: int,
        queue_timeout_s: float,
    ):
        self._make_agent = make_agent
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="agent")
        self._slots = asyncio.Semaphore(max_concurrent)
        self.queue_timeout_s = queue_timeout_s
        self.active = 0  # agent threads currently running (tests check nothing leaks)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def run(self, question: str, on_stage: Callable[[str], None]) -> RunOutcome:
        queued_at = time.monotonic()
        try:
            await asyncio.wait_for(self._slots.acquire(), self.queue_timeout_s)
        except TimeoutError:
            metrics.errors.add(1, {"code": "busy"})
            raise ApiError(
                503, "busy", "The service is busy. Please retry shortly.", retry_after=5
            ) from None
        queue_s = time.monotonic() - queued_at
        loop = asyncio.get_running_loop()

        def stage_from_thread(name: str) -> None:
            loop.call_soon_threadsafe(on_stage, name)

        control = RunControl(on_stage=stage_from_thread)
        try:
            agent = self._make_agent(control)
            future = loop.run_in_executor(
                self._executor, contextvars.copy_context().run, agent.ask, question
            )
        except BaseException:
            self._slots.release()
            raise
        self.active += 1

        def finished(_: asyncio.Future) -> None:
            self.active -= 1
            self._slots.release()

        future.add_done_callback(finished)
        try:
            result = await asyncio.shield(future)
        except asyncio.CancelledError:
            control.cancel()  # the thread stops at its next check (graph node or chunk)
            metrics.cancelled_runs.add(1)
            raise
        return RunOutcome(result, control, queue_s)


@dataclass
class Flight:
    key: str
    task: asyncio.Task = field(init=False)
    stages: list[str] = field(default_factory=list)
    waiters: int = 0
    _listeners: list[asyncio.Queue] = field(default_factory=list)

    def publish(self, stage: str) -> None:
        self.stages.append(stage)
        for queue in self._listeners:
            queue.put_nowait(stage)

    def listen(self) -> asyncio.Queue:
        """Stages so far, then each new one as it happens."""
        queue: asyncio.Queue = asyncio.Queue()
        for stage in self.stages:
            queue.put_nowait(stage)
        self._listeners.append(queue)
        return queue


class Flights:
    def __init__(self) -> None:
        self._flights: dict[str, Flight] = {}

    def __len__(self) -> int:
        return len(self._flights)

    @asynccontextmanager
    async def join(
        self, key: str, start: Callable[[Flight], Awaitable[Any]]
    ) -> AsyncIterator[tuple[Flight, bool]]:
        """Yields (flight, shared). `shared` is True when another request started it."""
        flight = self._flights.get(key)
        shared = flight is not None
        if flight is None:
            flight = Flight(key)
            flight.task = asyncio.create_task(start(flight))
            self._flights[key] = flight
            flight.task.add_done_callback(lambda task, f=flight: self._finished(f, task))
        else:
            metrics.coalesced.add(1)
        flight.waiters += 1
        try:
            yield flight, shared
        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                log.info("every client left; cancelling the run")
                flight.task.cancel()

    def _finished(self, flight: Flight, task: asyncio.Task) -> None:
        if self._flights.get(flight.key) is flight:
            del self._flights[flight.key]
        if not task.cancelled():
            task.exception()  # mark as retrieved; waiters re-raise it themselves
