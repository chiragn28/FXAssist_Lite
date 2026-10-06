"""Run an ASGI app with uvicorn on a free localhost port in a background thread.

Used by tests (CI-01: random free ports and readiness polling, no fixed sleeps) and by the
notebook's mock mode, where a real HTTP server is needed rather than an in-process transport.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import uvicorn


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ThreadedServer:
    def __init__(
        self,
        app,
        *,
        server_factory: Callable[[uvicorn.Config], uvicorn.Server] = uvicorn.Server,
        **config,
    ):
        self.port = free_port()
        config.setdefault("timeout_graceful_shutdown", 3)
        cfg = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on", **config
        )
        self.server = server_factory(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 15.0) -> ThreadedServer:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:  # readiness polling, not a fixed sleep (CI-01)
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.01)
        return self

    def stop(self, timeout: float = 15.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)


@contextmanager
def serve(app, **config) -> Iterator[ThreadedServer]:
    server = ThreadedServer(app, **config).start()
    try:
        yield server
    finally:
        server.stop()
