"""Gateway test harness. Real HTTP, no Docker, no network beyond localhost.

The gateway runs under uvicorn on a free port (CI-01) so that streaming, disconnects and
shutdown behave as they do in production. Its dependencies are in-memory stand-ins that a test
can break on purpose:
  - Redis: fakeredis (real Lua scripting); `h.redis_server.connected = False` = Redis down
  - PostgreSQL: in-memory key and log stores; `.fail = True` = database down
  - Qdrant: in-memory client behind a switch; `h.qdrant.down = True` = Qdrant down
  - LLM: the mock LLM server; reconfigure it through `h.mock`
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import fakeredis
import httpx
import pytest
from qdrant_client import QdrantClient

from fxassist_agent.chunking import Chunk, chunk_id
from fxassist_agent.embeddings import HashEmbedder
from fxassist_agent.llm import CircuitBreaker, OpenAICompatLLM
from fxassist_agent.sources import Source
from fxassist_agent.store import VectorStore
from fxassist_gateway.app import Components, assemble, create_app
from fxassist_gateway.auth import InMemoryKeyStore, KeyRecord, generate_key
from fxassist_gateway.cli import GatewayServer
from fxassist_gateway.config import GatewaySettings
from fxassist_gateway.logwriter import InMemoryLogStore
from fxassist_mock_llm.app import Behaviour
from fxassist_mock_llm.app import create_app as create_mock
from fxassist_mock_llm.server import ThreadedServer, serve
from gw_helpers import LEVERAGE, MARGIN, PIP  # also installs the MeterProvider first


class SwitchableQdrant:
    """Delegates to an in-memory QdrantClient unless `down` is set."""

    def __init__(self, client: QdrantClient):
        self._client = client
        self.down = False

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            if self.down:
                raise ConnectionRefusedError("qdrant is down (test)")
            return attr(*args, **kwargs)

        return call


def seed(store: VectorStore, version: str = "v1") -> None:
    store.ensure()
    for source_id, text in (("esma", LEVERAGE), ("wiki-margin", MARGIN), ("wiki-pip", PIP)):
        cid, digest = chunk_id(source_id, text)
        source = Source(
            id=source_id,
            title=source_id.title(),
            publisher="Test Publisher",
            url=f"https://example.org/{source_id}",
            format="html",
            licence="test",
            licence_url="https://example.org/licence",
            redistributable=True,
        )
        chunk = Chunk(cid, source_id, 0, None, text, digest)
        store.upsert(source, [chunk], store.embedder.embed_documents([text]))
    store.set_corpus_info({"corpus_version": version})


@dataclass
class Harness:
    settings: GatewaySettings
    mock_url: str
    key: str = ""
    redis_server: fakeredis.FakeServer = field(default_factory=fakeredis.FakeServer)
    keys: InMemoryKeyStore = field(default_factory=InMemoryKeyStore)
    logs: InMemoryLogStore = field(default_factory=InMemoryLogStore)
    postgres_up: bool = True
    qdrant: SwitchableQdrant | None = None
    store: VectorStore | None = None
    components: Components | None = None
    server: ThreadedServer | None = None
    mock: httpx.Client | None = None

    def __post_init__(self) -> None:
        self.key, key_id, key_hash = generate_key()
        self.key_id = key_id
        self.keys.records[key_id] = KeyRecord(key_id, key_hash, "test")
        embedder = HashEmbedder(dim=128)
        self.qdrant = SwitchableQdrant(QdrantClient(":memory:"))
        self.store = VectorStore(self.qdrant, "test_docs", embedder)  # type: ignore[arg-type]
        seed(self.store)

    async def factory(self, settings: GatewaySettings) -> Components:
        llm = OpenAICompatLLM(
            settings.resolved_llm_base_url,
            settings.llm_model,
            api_key="x",
            timeout_s=settings.llm_timeout_s,
            read_timeout_s=settings.llm_read_timeout_s,
            max_retries=settings.llm_max_retries,
            backoff_base_s=settings.llm_backoff_base_s,
            backoff_max_s=settings.llm_backoff_max_s,
            max_context_tokens=settings.llm_max_context_tokens,
            breaker=CircuitBreaker(settings.llm_breaker_failures, settings.llm_breaker_reset_s),
            trace_content=settings.trace_content,
        )

        async def postgres_ping() -> bool:
            return not self.logs.fail

        self.components = await assemble(
            settings,
            redis_client=fakeredis.FakeAsyncRedis(server=self.redis_server),
            key_store=self.keys,
            log_store=self.logs,
            store=self.store,
            embedder=self.store.embedder,
            llm=llm,
            postgres_ping=postgres_ping,
        )
        return self.components

    # --- HTTP helpers ---------------------------------------------------------------------

    @property
    def url(self) -> str:
        return self.server.url

    def client(self, **kwargs) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=kwargs.pop("timeout", 20), **kwargs)

    def headers(self, key: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {key or self.key}"}

    def ask(self, question: str, *, stream: bool = False, key: str | None = None, **kw):
        with self.client() as c:
            return c.post(
                "/v1/ask",
                json={"question": question, "stream": stream},
                headers={**self.headers(key), **kw.pop("headers", {})},
                **kw,
            )

    def mock_config(self, **patch) -> None:
        self.mock.post("/_mock/config", json=patch).raise_for_status()

    def mock_stats(self) -> dict:
        return self.mock.get("/_mock/stats").json()


@pytest.fixture(scope="session")
def mock_server() -> Iterator[ThreadedServer]:
    with serve(create_mock(Behaviour(models=["mock-llm"]))) as server:
        yield server


def make_settings(tmp_path: Path, mock_url: str, **overrides) -> GatewaySettings:
    base = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "collection": "test_docs",
        "top_k": 3,
        "score_threshold": 0.2,
        "out_of_scope_threshold": 0.1,
        "llm_base_url": f"{mock_url}/v1",
        "llm_model": "mock-llm",
        "llm_timeout_s": 5.0,
        "llm_read_timeout_s": 2.0,
        "llm_backoff_base_s": 0.01,
        "llm_backoff_max_s": 0.05,
        "rate_limit_per_minute": 6000,
        "rate_limit_burst": 100,
        "key_refresh_s": 0.2,
        "redis_retry_after_s": 0.5,
        "index_info_ttl_s": 0.0,
        "ready_llm_cache_s": 0.0,
        "sse_keepalive_s": 0.2,
        "queue_timeout_s": 5.0,
    }
    return GatewaySettings(**(base | overrides))


@pytest.fixture
def gateway_factory(tmp_path, mock_server):
    """Start a gateway with settings overrides: `h = gateway_factory(cache_enabled=False)`.
    `before_start=fn(harness)` can break dependencies before the gateway starts."""
    started: list[Harness] = []
    mock = httpx.Client(base_url=mock_server.url, timeout=10)
    mock.delete("/_mock/config").raise_for_status()

    def start(before_start=None, **overrides) -> Harness:
        settings = make_settings(tmp_path, mock_server.url, **overrides)
        h = Harness(settings=settings, mock_url=mock_server.url, mock=mock)
        if before_start is not None:
            before_start(h)
        app = create_app(settings, components_factory=h.factory)

        def server_factory(config):
            return GatewayServer(config, on_drain=lambda: setattr(app.state, "draining", True))

        h.app = app
        h.server = ThreadedServer(app, server_factory=server_factory).start()
        started.append(h)
        return h

    yield start
    for h in started:
        h.server.stop()
    mock.close()


@pytest.fixture
def gw(gateway_factory) -> Harness:
    return gateway_factory()
