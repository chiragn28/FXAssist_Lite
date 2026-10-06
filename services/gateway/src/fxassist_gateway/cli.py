"""`fxassist-gateway` command: serve the API and manage API keys."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable
from types import FrameType

import uvicorn

from .auth import KeyRecord, generate_key
from .config import GatewaySettings

log = logging.getLogger(__name__)


class GatewayServer(uvicorn.Server):
    """uvicorn, plus a draining flag set the moment SIGTERM/SIGINT arrives (API-08).

    On a signal uvicorn stops accepting connections, then waits up to
    `timeout_graceful_shutdown` for in-flight requests (including streams) to finish. Between
    the signal and the socket closing, and on keep-alive connections, new requests could still
    arrive: the draining flag makes them fail fast with 503 + `Connection: close`, and /readyz
    report "shutting_down" so a load balancer stops sending traffic.
    """

    def __init__(self, config: uvicorn.Config, on_drain: Callable[[], None]):
        super().__init__(config)
        self.on_drain = on_drain

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self.on_drain()
        log.info("signal %s received: draining", sig)
        super().handle_exit(sig, frame)


def serve(settings: GatewaySettings) -> None:
    from .app import configure_logging, create_app
    from .telemetry import setup_telemetry

    configure_logging(os.environ.get("FXA_LOG_LEVEL", "INFO"))
    tracer_provider = setup_telemetry(settings)
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=os.environ.get("FXA_GATEWAY_HOST", "127.0.0.1"),
        port=settings.gateway_port,
        timeout_graceful_shutdown=int(settings.shutdown_grace_s),
        log_config=None,  # keep our format, which includes the request ID
        proxy_headers=False,
        server_header=False,
    )
    try:
        GatewayServer(config, on_drain=lambda: setattr(app.state, "draining", True)).run()
    finally:
        tracer_provider.shutdown()  # flushes queued spans, bounded by the export timeout


async def _with_store(settings: GatewaySettings, fn):
    from .db import PostgresStore

    store = PostgresStore(settings.postgres_conninfo, max_size=1)
    await store.pool.open(wait=True, timeout=10)
    try:
        return await fn(store)
    finally:
        await store.close()


def create_key(settings: GatewaySettings, name: str) -> int:
    key, key_id, key_hash = generate_key()

    async def add(store):
        await store.add_key(KeyRecord(key_id, key_hash, name))

    asyncio.run(_with_store(settings, add))
    print(
        f"Created API key '{name}' (id {key_id}). It is shown once; store it now:", file=sys.stderr
    )
    print(key)
    return 0


def revoke_key(settings: GatewaySettings, key_id: str) -> int:
    ok = asyncio.run(_with_store(settings, lambda store: store.revoke_key(key_id)))
    print(f"revoked {key_id}" if ok else f"no active key with id {key_id}")
    return 0 if ok else 1


def list_keys(settings: GatewaySettings) -> int:
    rows = asyncio.run(_with_store(settings, lambda store: store.list_keys()))
    for key_id, name, created, revoked in rows:
        state = f"revoked {revoked:%Y-%m-%d}" if revoked else "active"
        print(f"{key_id}  {name:<20} created {created:%Y-%m-%d}  {state}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fxassist-gateway", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the API server")
    p = sub.add_parser("create-key", help="create an API key and print it once")
    p.add_argument("--name", required=True)
    p = sub.add_parser("revoke-key", help="revoke an API key by its id")
    p.add_argument("key_id")
    sub.add_parser("list-keys", help="list API keys (ids and names only)")
    args = parser.parse_args(argv)
    settings = GatewaySettings()
    try:
        if args.command == "serve":
            serve(settings)
            return 0
        if args.command == "create-key":
            return create_key(settings, args.name)
        if args.command == "revoke-key":
            return revoke_key(settings, args.key_id)
        return list_keys(settings)
    except Exception as exc:  # psycopg errors: say what to do, not a stack trace
        if type(exc).__module__.startswith(("psycopg", "psycopg_pool")):
            print(
                f"error: cannot use PostgreSQL ({type(exc).__name__}). Start it with: make up",
                file=sys.stderr,
            )
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
