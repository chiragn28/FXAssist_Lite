"""Request IDs (API-06) and draining during shutdown (API-08), as plain ASGI middleware.

Plain ASGI, not Starlette's BaseHTTPMiddleware: that one wraps the response body in its own
stream, which gets in the way of streaming and of noticing client disconnects.
"""

from __future__ import annotations

import contextvars
import json
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
                message["headers"] = [*message.get("headers", []), header]
            await send(message)

        try:
            if self.draining() and scope.get("path") not in ("/healthz", "/readyz"):
                await self._reject_draining(send_with_id, request_id)
                return
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)

    @staticmethod
    async def _reject_draining(send: Send, request_id: str) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "shutting_down",
                    "message": "This instance is shutting down. Please retry.",
                    "request_id": request_id,
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"1"),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
