"""Split page text into chunks with stable, content-derived IDs.

DAT-02: a chunk's ID is a UUID derived from its source and the hash of its text, so
ingesting the same document twice produces the same IDs and Qdrant upserts are idempotent.
DAT-03: when a document changes, its changed chunks get new IDs and the old ones can be found
and removed.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .extract import Page, is_non_english

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/fxassist-lite/chunks")


@dataclass(frozen=True)
class Chunk:
    id: str
    source_id: str
    index: int
    page: int | None
    text: str
    content_hash: str


@dataclass
class ChunkStats:
    pages: int = 0
    empty_pages: int = 0
    chunks: int = 0
    too_short: int = 0
    duplicates: int = 0
    non_english: int = 0
    capped: bool = False
    warnings: list[str] = field(default_factory=list)


def chunk_id(source_id: str, text: str) -> tuple[str, str]:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return str(uuid.uuid5(_NAMESPACE, f"{source_id}:{digest}")), digest


def make_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", "; ", ", ", " ", ""],
        keep_separator="end",
    )


def chunk_pages(
    source_id: str,
    pages: Iterable[Page],
    splitter: RecursiveCharacterTextSplitter,
    stats: ChunkStats,
    *,
    min_chars: int,
    max_chunks: int,
) -> Iterator[Chunk]:
    """Yield chunks lazily, page by page, so large documents stream through (DAT-04)."""
    seen: set[str] = set()
    index = 0
    for page in pages:
        stats.pages += 1
        if not page.text.strip():
            stats.empty_pages += 1
            continue
        for piece in splitter.split_text(page.text):
            text = piece.strip()
            if len(text) < min_chars:  # DAT-01: never embed empty or near-empty chunks
                stats.too_short += 1
                continue
            if is_non_english(text):  # DAT-06: never silently embed another language
                stats.non_english += 1
                continue
            cid, digest = chunk_id(source_id, text)
            if cid in seen:
                stats.duplicates += 1
                continue
            if index >= max_chunks:  # DAT-04: cap with a warning instead of exhausting memory
                stats.capped = True
                stats.warnings.append(
                    f"chunk cap of {max_chunks} reached; rest of document skipped"
                )
                return
            seen.add(cid)
            stats.chunks += 1
            yield Chunk(cid, source_id, index, page.number, text, digest)
            index += 1
