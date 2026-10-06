"""Request IDs (API-06) and draining during shutdown (API-08), as plain ASGI middleware.

Draining: once SIGTERM arrives, uvicorn stops accepting connections and readiness reports
"shutting_down". Requests that still arrive come over keep-alive connections opened earlier.
They are served, with `Connection: close`, so the client's next request opens a new connection
to a pod that is not stopping. (The first version answered them 503; the kind rollout test showed
that turns every rolling update into user-visible errors.)

Plain ASGI, not Starlette's BaseHTTPMiddleware: that one wraps the response body in its own
stream, which gets in the way of streaming and of noticing client disconnects.
"""

from __future__ import annotations

import contextvars
import logging
import re
import uuid
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every log record, so log lines can be joined to a request."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def incoming_request_id(scope: Scope) -> str:
    """Reuse the caller's X-Request-ID if it is sane (no log injection, bounded), else new."""
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if _VALID_ID.match(candidate):
                return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, draining: Callable[[], bool]):
        self.app = app
        self.draining = draining

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = incoming_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        header = (b"x-request-id", request_id.encode())

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                extra = [header]
                if self.draining():  # send the client to another pod for its next request
                    extra.append((b"connection", b"close"))
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)
