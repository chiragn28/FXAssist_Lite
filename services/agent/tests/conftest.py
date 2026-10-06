"""Shared fixtures: everything runs offline (no Qdrant server, no model, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from fxassist_agent.config import Settings
from fxassist_agent.embeddings import HashEmbedder
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
