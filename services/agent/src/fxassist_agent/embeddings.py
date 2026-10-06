"""Text embeddings on CPU (ADR-009, ADR-023).

`FastEmbedder` runs BAAI/bge-small-en-v1.5 through fastembed (ONNX Runtime).
`HashEmbedder` is a deterministic stand-in for tests: no download, no model, same API.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class Embedder(Protocol):
    model_name: str
    runtime: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    runtime = "fastembed-onnx"

    def __init__(self, model_name: str, dim: int, *, cache_dir: Path, batch_size: int = 64):
        from fastembed import TextEmbedding  # imported lazily: loading ONNX Runtime is slow

        self.model_name = model_name
        self.dim = dim
        self._batch_size = batch_size
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [
            v.tolist() for v in self._model.passage_embed(list(texts), batch_size=self._batch_size)
        ]
        self._check(vectors)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vector = next(iter(self._model.query_embed(text))).tolist()
        self._check([vector])
        return vector

    def _check(self, vectors: list[list[float]]) -> None:
        if vectors and len(vectors[0]) != self.dim:
            raise ValueError(
                f"{self.model_name} produced {len(vectors[0])}-dim vectors, expected {self.dim}"
            )


class HashEmbedder:
    """Bag-of-words hashed into a fixed number of buckets, L2-normalised.

    Texts sharing words get high cosine similarity, which is enough to test retrieval logic
    deterministically and offline.
    """

    runtime = "hash-test"

    def __init__(self, dim: int = 64, model_name: str = "hash-bow-test"):
        self.dim = dim
        self.model_name = model_name

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            bucket = int(hashlib.md5(word.encode(), usedforsecurity=False).hexdigest(), 16)
            vec[bucket % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)
