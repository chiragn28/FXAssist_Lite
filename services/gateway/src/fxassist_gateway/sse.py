"""Server-Sent Events: formatting, and a response that copes with bad networks.

API-05: proxies (nginx, some ingress controllers, corporate proxies) buffer responses by
default, which turns a stream into one late blob. The headers below ask them not to:
`X-Accel-Buffering: no` (nginx), `Cache-Control: no-cache, no-transform` (caches and
compressing proxies). Keep-alive comments every few seconds stop idle-timeout proxies from
closing a connection while the agent is still working.

API-07: the server must not buffer without limit for a client that stops reading. Each write
waits for the socket to drain; if one write takes longer than `write_timeout_s`, the stream is
abandoned and the client's run is released.

LLM-05: a task watches for the client disconnecting. When it does, the event generator is
closed, which leaves the flight, which cancels the agent run if no one else is waiting.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, suppress
from typing import Any

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from . import metrics

log = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def event(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


KEEPALIVE = b": keep-alive\n\n"


class EventStreamResponse(Response):
    media_type = "text/event-stream"

    def __init__(
        self,
        events: AsyncIterator[bytes],
        *,
        write_timeout_s: float,
        headers: dict[str, str] | None = None,
        on_finish: Callable[[str], None] | None = None,
    ):
        # Like StreamingResponse: no body attribute, so no Content-Length header.
        self.status_code = 200
        self.background = None
        self.init_headers({**SSE_HEADERS, **(headers or {})})
        self.events = events
        self.write_timeout_s = write_timeout_s
        self.on_finish = on_finish
        self.end_reason = "completed"  # completed | client_disconnected | slow_client

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
        pump = asyncio.create_task(self._pump(send))
        watcher = asyncio.create_task(self._watch_disconnect(receive))
        try:
            done, _ = await asyncio.wait({pump, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done and not pump.done():
                self.end_reason = "client_disconnected"
                pump.cancel()
            with suppress(asyncio.CancelledError):
                await pump
        finally:
            watcher.cancel()
            pump.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            if self.on_finish is not None:
                self.on_finish(self.end_reason)

    async def _watch_disconnect(self, receive: Receive) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return

    async def _pump(self, send: Send) -> None:
        async with aclosing(self.events) as events:
            async for chunk in events:
                try:
                    await asyncio.wait_for(
                        send({"type": "http.response.body", "body": chunk, "more_body": True}),
                        self.write_timeout_s,
                    )
                except TimeoutError:
                    self.end_reason = "slow_client"
                    metrics.slow_clients.add(1)
                    log.warning(
                        "client did not read for %.0fs; abandoning the stream", self.write_timeout_s
                    )
                    return
                except OSError:
                    self.end_reason = "client_disconnected"
                    return
        await send({"type": "http.response.body", "body": b"", "more_body": False})
