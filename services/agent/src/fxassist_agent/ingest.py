"""Ingestion: raw files -> pages -> chunks -> embeddings -> Qdrant, with a written report.

Guarantees (tests in services/agent/tests/test_ingest.py):
- DAT-01 a document with no extractable text is skipped with a warning; nothing empty is embedded
- DAT-02 re-running ingestion adds no duplicates (stable IDs; unchanged chunks are not re-embedded)
- DAT-03 a changed document's old chunks are removed and the corpus version changes
- DAT-04 documents stream through in batches; a chunk cap stops runaway documents
- DAT-06 non-English chunks are skipped and counted
- DAT-07 a missing or broken file does not stop the other documents
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .chunking import Chunk, ChunkStats, chunk_pages, make_splitter
from .config import Settings
from .embeddings import Embedder
from .extract import iter_pages
from .fetch import raw_path
from .sources import Source
from .store import VectorStore

log = logging.getLogger(__name__)


@dataclass
class DocReport:
    source_id: str
    status: str  # ingested | skipped | missing | failed
    pages: int = 0
    chunks: int = 0
    embedded: int = 0  # chunks that were new and had to be embedded
    removed: int = 0  # stale chunks deleted after the document changed
    too_short: int = 0
    duplicates: int = 0
    non_english: int = 0
    capped: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class IngestReport:
    started_at: str
    settings: dict
    docs: list[DocReport] = field(default_factory=list)
    removed_unlisted: int = 0
    total_points: int = 0
    corpus_version: str = ""

    def summary(self) -> str:
        by_status: dict[str, int] = {}
        for d in self.docs:
            by_status[d.status] = by_status.get(d.status, 0) + 1
        lines = [
            f"documents: {len(self.docs)} ({', '.join(f'{k} {v}' for k, v in sorted(by_status.items()))})",
            f"chunks in collection: {self.total_points}",
            f"newly embedded: {sum(d.embedded for d in self.docs)}, "
            f"removed as stale: {sum(d.removed for d in self.docs) + self.removed_unlisted}",
            f"corpus version: {self.corpus_version}",
        ]
        for d in self.docs:
            if d.status != "ingested" or d.warnings:
                lines.append(f"  {d.status.upper():8} {d.source_id}: {'; '.join(d.warnings)}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def _flush(
    store: VectorStore,
    embedder: Embedder,
    source: Source,
    batch: list[Chunk],
    existing: set[str],
    title_prefix: bool,
) -> int:
    """Embed and upsert only the chunks not already stored. Returns how many were embedded."""
    new = [c for c in batch if c.id not in existing]
    if not new:
        return 0
    texts = [f"{source.title}\n\n{c.text}" if title_prefix else c.text for c in new]
    store.upsert(source, new, embedder.embed_documents(texts))
    return len(new)


def ingest_source(
    source: Source,
    path: Path,
    store: VectorStore,
    embedder: Embedder,
    settings: Settings,
    *,
    title_prefix: bool = True,
) -> DocReport:
    report = DocReport(source.id, "ingested")
    stats = ChunkStats()
    splitter = make_splitter(settings.chunk_size, settings.chunk_overlap)
    existing = store.ids_for_source(source.id)
    seen: set[str] = set()
    batch: list[Chunk] = []
    for chunk in chunk_pages(
        source.id,
        iter_pages(source, path),
        splitter,
        stats,
        min_chars=settings.min_chunk_chars,
        max_chunks=settings.max_chunks_per_doc,
    ):
        seen.add(chunk.id)
        batch.append(chunk)
        if len(batch) >= settings.embed_batch_size:
            report.embedded += _flush(store, embedder, source, batch, existing, title_prefix)
            batch = []
    report.embedded += _flush(store, embedder, source, batch, existing, title_prefix)

    report.pages, report.chunks = stats.pages, stats.chunks
    report.too_short, report.duplicates = stats.too_short, stats.duplicates
    report.non_english, report.capped = stats.non_english, stats.capped
    report.warnings.extend(stats.warnings)

    if stats.chunks == 0:
        # DAT-01: scanned PDFs and empty pages end up here. Keep any earlier chunks rather than
        # deleting good data because of an extraction failure.
        report.status = "skipped"
        report.warnings.append(
            f"no extractable text in {stats.pages} page(s) (scanned PDF or empty page?)"
        )
        return report

    judged = stats.chunks + stats.non_english
    if stats.non_english:
        report.warnings.append(f"{stats.non_english} of {judged} chunks skipped as non-English")

    stale = sorted(existing - seen)  # DAT-03: chunks from the previous version of the document
    store.delete_ids(stale)
    report.removed = len(stale)
    return report


def ingest_all(
    sources: list[Source], raw_dir: Path, store: VectorStore, embedder: Embedder, settings: Settings
) -> IngestReport:
    report = IngestReport(
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        settings={
            "embed_model": embedder.model_name,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "max_chunks_per_doc": settings.max_chunks_per_doc,
        },
    )
    store.ensure()
    for source in sources:
        path = raw_path(raw_dir, source)
        if not path.exists():
            report.docs.append(
                DocReport(source.id, "missing", warnings=["raw file not found; run make fetch"])
            )
            continue
        try:
            report.docs.append(ingest_source(source, path, store, embedder, settings))
        except Exception as exc:  # one broken file must not stop the run (DAT-07)
            log.exception("ingestion failed for %s", source.id)
            report.docs.append(
                DocReport(source.id, "failed", warnings=[f"{type(exc).__name__}: {exc}"])
            )

    report.removed_unlisted = store.delete_sources_except([s.id for s in sources])
    report.total_points = store.count()
    report.corpus_version = store.compute_corpus_version()
    store.set_corpus_info(
        {
            "corpus_version": report.corpus_version,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "ingested_at": report.started_at,
        }
    )
    return report
