"""Qdrant access: one collection, with the embedding model recorded on it.

DAT-10: the collection's metadata records the embedding model, runtime and dimension. The agent
compares them with its own configuration and refuses to run on a mismatch, because vectors
from different models are not comparable and search would return nonsense without any error.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from .chunking import Chunk
from .embeddings import Embedder
from .sources import Source

SCHEMA_VERSION = 1


class StoreError(RuntimeError):
    """Base class for vector store problems the user can act on."""


class EmbeddingMismatchError(StoreError):
    pass


class CollectionMissingError(StoreError):
    pass


@dataclass(frozen=True)
class Hit:
    id: str
    score: float
    source_id: str
    title: str
    publisher: str
    url: str
    page: int | None
    text: str
    vector: list[float] | None = None


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str, embedder: Embedder):
        self.client = client
        self.collection = collection
        self.embedder = embedder

    # --- Collection lifecycle ----------------------------------------------------

    def expected_metadata(self) -> dict:
        return {
            "embedding_model": self.embedder.model_name,
            "embedding_runtime": self.embedder.runtime,
            "embedding_dim": self.embedder.dim,
            "schema_version": SCHEMA_VERSION,
        }

    def exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def create(self) -> None:
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(
                size=self.embedder.dim, distance=models.Distance.COSINE
            ),
            metadata=self.expected_metadata(),
        )
        self.client.create_payload_index(
            self.collection, field_name="source_id", field_schema=models.PayloadSchemaType.KEYWORD
        )

    def drop(self) -> None:
        if self.exists():
            self.client.delete_collection(self.collection)

    def metadata(self) -> dict:
        return dict(self.client.get_collection(self.collection).config.metadata or {})

    def check_compatible(self) -> None:
        if not self.exists():
            raise CollectionMissingError(
                f"Qdrant collection '{self.collection}' does not exist. Run: make ingest"
            )
        actual = self.metadata()
        expected = self.expected_metadata()
        diffs = {k: (actual.get(k), v) for k, v in expected.items() if actual.get(k) != v}
        if diffs:
            detail = "; ".join(
                f"{k}: collection has {a!r}, agent uses {e!r}" for k, (a, e) in diffs.items()
            )
            raise EmbeddingMismatchError(
                f"Collection '{self.collection}' was built with a different embedding setup "
                f"({detail}). Vectors are not comparable. Rebuild it: make ingest ARGS=--rebuild"
            )

    def ensure(self) -> None:
        if self.exists():
            self.check_compatible()
        else:
            self.create()

    # --- Writes ------------------------------------------------------------------

    def upsert(
        self, source: Source, chunks: Sequence[Chunk], vectors: Sequence[list[float]]
    ) -> None:
        points = [
            models.PointStruct(
                id=c.id,
                vector=v,
                payload={
                    "source_id": c.source_id,
                    "title": source.title,
                    "publisher": source.publisher,
                    "url": source.url,
                    "page": c.page,
                    "chunk_index": c.index,
                    "text": c.text,
                    "content_hash": c.content_hash,
                    "redistributable": source.redistributable,
                },
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        if points:
            self.client.upsert(self.collection, points=points, wait=True)

    def ids_for_source(self, source_id: str) -> set[str]:
        flt = models.Filter(
            must=[models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id))]
        )
        return self._scroll_ids(flt)

    def all_ids(self) -> set[str]:
        return self._scroll_ids(None)

    def _scroll_ids(self, flt: models.Filter | None) -> set[str]:
        ids: set[str] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                scroll_filter=flt,
                limit=1000,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            ids.update(str(p.id) for p in points)
            if offset is None:
                return ids

    def delete_ids(self, ids: Sequence[str]) -> None:
        if ids:
            self.client.delete(
                self.collection, points_selector=models.PointIdsList(points=list(ids)), wait=True
            )

    def delete_sources_except(self, keep: Sequence[str]) -> int:
        """Remove chunks of documents that are no longer in the registry."""
        flt = (
            models.Filter(
                must_not=[
                    models.FieldCondition(key="source_id", match=models.MatchAny(any=list(keep)))
                ]
            )
            if keep
            else None
        )
        stale = self._scroll_ids(flt)
        self.delete_ids(sorted(stale))
        return len(stale)

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count

    # --- Corpus version (DAT-03, ADR-010) ----------------------------------------

    def compute_corpus_version(self) -> str:
        """Hash of every chunk ID: changes whenever any document's content changes."""
        digest = hashlib.sha256("\n".join(sorted(self.all_ids())).encode()).hexdigest()
        return digest[:16]

    def set_corpus_info(self, info: dict) -> None:
        self.client.update_collection(self.collection, metadata=info)

    def corpus_version(self) -> str | None:
        return self.metadata().get("corpus_version")

    # --- Reads -------------------------------------------------------------------

    def search(self, vector: list[float], limit: int) -> list[Hit]:
        result = self.client.query_points(
            self.collection, query=vector, limit=limit, with_payload=True, with_vectors=True
        )
        hits = []
        for p in result.points:
            payload = p.payload or {}
            hits.append(
                Hit(
                    id=str(p.id),
                    score=float(p.score),
                    source_id=payload.get("source_id", ""),
                    title=payload.get("title", ""),
                    publisher=payload.get("publisher", ""),
                    url=payload.get("url", ""),
                    page=payload.get("page"),
                    text=payload.get("text", ""),
                    vector=list(p.vector) if isinstance(p.vector, list) else None,
                )
            )
        return hits
