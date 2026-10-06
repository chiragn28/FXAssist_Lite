"""Shared fixtures: everything runs offline (no Qdrant server, no model, no network)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from fxassist_agent.config import Settings
from fxassist_agent.embeddings import HashEmbedder
from fxassist_agent.sources import Source
from fxassist_agent.store import VectorStore


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        eval_dir=tmp_path / "eval",
        chunk_size=300,
        chunk_overlap=30,
        min_chunk_chars=20,
        embed_batch_size=4,
        top_k=3,
        score_threshold=0.2,
        out_of_scope_threshold=0.1,
        max_per_source=2,
    )


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder(dim=128)


@pytest.fixture
def store(embedder: HashEmbedder) -> VectorStore:
    return VectorStore(QdrantClient(":memory:"), "test_docs", embedder)


def make_source(source_id: str, fmt: str = "html", *, title: str | None = None) -> Source:
    return Source(
        id=source_id,
        title=title or source_id.replace("-", " ").title(),
        publisher="Test Publisher",
        url=f"https://example.org/{source_id}",
        format=fmt,  # type: ignore[arg-type]
        licence="test",
        licence_url="https://example.org/licence",
        redistributable=True,
    )


def html_page(*paragraphs: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<html><head><title>t</title></head><body>"
        "<nav>Home | About | Contact</nav>"
        f"<main><article><h1>Article</h1>{body}</article></main>"
        "<footer>Copyright footer text</footer></body></html>"
    )


class FakeLLM:
    """Scripted OpenAI-compatible stand-in. `script` maps a call kind to responses.

    Call kinds: "grade" (json_mode), "rewrite" (system prompt mentions search query), "answer".
    A response may be a string or a callable taking the messages.
    """

    model = "fake-model"

    def __init__(self, **script: str | list[str] | Callable[[list[dict]], str]):
        self.script = {
            k: (iter(v) if isinstance(v, list) else itertools.repeat(v)) for k, v in script.items()
        }
        self.calls: list[tuple[str, list[dict]]] = []

    @staticmethod
    def kind(messages: list[dict], json_mode: bool) -> str:
        if json_mode:
            return "grade"
        if "search query" in messages[0]["content"]:
            return "rewrite"
        return "answer"

    def complete(self, messages, *, max_tokens, temperature=0.0, json_mode=False) -> str:
        kind = self.kind(messages, json_mode)
        self.calls.append((kind, messages))
        response = next(self.script[kind])
        return response(messages) if callable(response) else response

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)
