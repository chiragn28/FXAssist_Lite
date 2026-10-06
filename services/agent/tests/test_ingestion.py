"""Ingestion edge cases: DAT-01 to DAT-08 and DAT-10."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from qdrant_client import QdrantClient

from agent_helpers import html_page, make_source
from fxassist_agent.chunking import ChunkStats, chunk_id, chunk_pages, make_splitter
from fxassist_agent.embeddings import HashEmbedder
from fxassist_agent.extract import (
    Page,
    english_ratio,
    extract_html,
    extract_wikipedia,
    is_non_english,
    learn_boilerplate,
    normalise,
    strip_boilerplate,
)
from fxassist_agent.fetch import fetch_all, raw_path
from fxassist_agent.ingest import ingest_all
from fxassist_agent.sources import RegistryError, load_sources
from fxassist_agent.store import CollectionMissingError, EmbeddingMismatchError, VectorStore

ROOT = Path(__file__).resolve().parents[3]

ENGLISH = (
    "Leverage allows a trader to open a position that is much larger than the money in the "
    "account. If the market moves against the position, the losses can be larger than the "
    "deposit, and the provider may close the position when the margin falls too low."
)
MORE_ENGLISH = (
    "A margin call is a demand from the provider for more funds when the account value falls "
    "below the level that is required to keep the open positions, and it can come at short notice."
)
FRENCH = (
    "L'effet de levier permet d'ouvrir une position beaucoup plus grande que le capital du "
    "compte. Si le marché évolue contre la position, les pertes peuvent dépasser le dépôt initial "
    "et le fournisseur peut clôturer la position lorsque la marge devient insuffisante."
)


def write_raw(settings, source, content: str | bytes) -> None:
    path = raw_path(settings.raw_dir, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def blank_pdf(pages: int = 3) -> bytes:
    """A PDF with no text layer, like a scanned document (DAT-01)."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# --- Registry (DAT-08) ---------------------------------------------------------------


def test_dat08_real_registry_loads_and_every_source_has_licence_terms() -> None:
    sources = load_sources(ROOT / "data" / "sources.yaml")
    assert 20 <= len(sources) <= 40
    for s in sources:
        assert s.licence and s.licence_url.startswith("https://")
        assert isinstance(s.redistributable, bool)
    assert any(not s.redistributable for s in sources), "fetch-only path should be exercised"


def test_dat08_sources_md_lists_every_source() -> None:
    md = (ROOT / "data" / "SOURCES.md").read_text(encoding="utf-8")
    for s in load_sources(ROOT / "data" / "sources.yaml"):
        assert f"`{s.id}`" in md


def test_dat08_raw_documents_are_git_ignored() -> None:
    import subprocess

    probe = "data/raw/any-document.pdf"
    assert (
        subprocess.run(["git", "check-ignore", "-q", probe], cwd=ROOT, check=False).returncode == 0
    )


def test_registry_rejects_duplicates_and_missing_fields(tmp_path: Path) -> None:
    base = "id: a\ntitle: t\npublisher: p\nurl: u\nformat: html\nlicence: l\nlicence_url: x\nredistributable: true"
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "sources:\n  - " + base.replace("\n", "\n    ") + "\n  - " + base.replace("\n", "\n    ")
    )
    with pytest.raises(RegistryError, match="duplicate"):
        load_sources(dup)
    missing = tmp_path / "missing.yaml"
    missing.write_text("sources:\n  - id: a\n    title: t\n")
    with pytest.raises(RegistryError, match="missing"):
        load_sources(missing)


# --- Fetch (DAT-07) ------------------------------------------------------------------


def test_dat07_failures_are_reported_and_leave_no_partial_files(tmp_path: Path) -> None:
    ok, gone, broken, flaky = (make_source(i) for i in ("ok", "gone", "broken", "flaky"))
    pdf_but_html = make_source("not-a-pdf", "pdf")
    attempts = {"flaky": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.strip("/")
        if name == "gone":
            return httpx.Response(404)
        if name == "broken":
            raise httpx.ConnectError("connection refused")
        if name == "flaky":
            attempts["flaky"] += 1
            return (
                httpx.Response(503)
                if attempts["flaky"] == 1
                else httpx.Response(200, text=html_page(ENGLISH))
            )
        if name == "not-a-pdf":
            return httpx.Response(200, text="<html>moved</html>")
        return httpx.Response(200, text=html_page(ENGLISH))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    raw = tmp_path / "raw"
    report = fetch_all([ok, gone, broken, flaky, pdf_but_html], raw, client=client)

    status = {r.source_id: r.status for r in report.results}
    assert status == {
        "ok": "downloaded",
        "gone": "failed",
        "broken": "failed",
        "flaky": "downloaded",
        "not-a-pdf": "failed",
    }
    assert {r.source_id for r in report.failed} == {"gone", "broken", "not-a-pdf"}
    assert "404" in next(r.error for r in report.failed if r.source_id == "gone")
    files = sorted(p.name for p in raw.iterdir())
    assert files == ["flaky.html", "flaky.meta.json", "ok.html", "ok.meta.json"]  # nothing partial


def test_dat07_second_fetch_uses_cache(tmp_path: Path) -> None:
    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: calls.append(r) or httpx.Response(200, text="x" * 50)
        )
    )
    src = make_source("doc")
    fetch_all([src], tmp_path, client=client)
    report = fetch_all([src], tmp_path, client=client)
    assert report.results[0].status == "cached" and len(calls) == 1


# --- Extraction (DAT-05, DAT-06) ---------------------------------------------------


def test_dat05_repeated_headers_footers_and_page_numbers_are_removed() -> None:
    bodies = [
        "Leverage rules.",
        "Margin calls.",
        "Pip values.",
        "Spreads widen.",
        "Close-out levels.",
        "Risk warnings.",
        "Inducements banned.",
    ]
    pages = [
        f"REGULATORY GUIDE 227: CFDs\n© ASIC 2011 Page {n}\n{body}\n{n}"
        for n, body in enumerate(bodies, start=1)
    ]
    boilerplate = learn_boilerplate(pages)
    cleaned = strip_boilerplate(pages[3], boilerplate)
    assert "REGULATORY GUIDE" not in cleaned and "© ASIC" not in cleaned
    assert cleaned.strip() == "Spreads widen."
    # Known limitation: lines are compared with digits ignored (so "Page 6" matches "Page 7"),
    # which means body lines identical except for numbers on most pages would also be removed.


def test_dat05_html_keeps_main_content_only() -> None:
    text = extract_html(html_page(ENGLISH, MORE_ENGLISH))
    assert "margin call" in text
    assert "Home | About" not in text and "Copyright footer" not in text


def test_normalise_joins_hyphenated_line_breaks_and_collapses_space() -> None:
    assert normalise("an exam-\nple   of\n\n\n\ntext") == "an example of\n\ntext"


def test_wikipedia_extract_drops_reference_sections() -> None:
    import json

    api = {
        "query": {
            "pages": [
                {
                    "extract": "Intro text.\n\n== History ==\nOld.\n\n== References ==\n[1] cite",
                    "revisions": [{"revid": 1}],
                }
            ]
        }
    }
    text = extract_wikipedia(json.dumps(api))
    assert "History" in text and "cite" not in text and "References" not in text


def test_dat06_language_check() -> None:
    assert english_ratio(ENGLISH) > 0.25
    assert is_non_english(FRENCH)
    assert not is_non_english(ENGLISH)
    assert english_ratio("EUR/USD 1.0850 1.0852 0.2") is None  # too few words to judge


# --- Chunking (DAT-02, DAT-04) -------------------------------------------------------


def test_dat02_chunk_ids_are_stable_and_source_specific() -> None:
    assert chunk_id("a", "same text") == chunk_id("a", "same text")
    assert chunk_id("a", "same text")[0] != chunk_id("b", "same text")[0]
    assert chunk_id("a", "same text")[0] != chunk_id("a", "other text")[0]


def test_dat04_large_document_streams_and_is_capped() -> None:
    consumed = {"pages": 0}

    def pages():  # a 500-page document, produced lazily
        for n in range(1, 501):
            consumed["pages"] += 1
            yield Page(n, f"Page {n}. " + ENGLISH)

    stats = ChunkStats()
    stream = chunk_pages("big", pages(), make_splitter(300, 30), stats, min_chars=20, max_chunks=50)
    first = next(stream)
    assert first.page == 1 and consumed["pages"] == 1  # nothing read ahead
    rest = list(stream)
    assert len(rest) + 1 == 50
    assert stats.capped and "cap" in stats.warnings[0]
    assert consumed["pages"] < 500


# --- Ingestion pipeline ----------------------------------------------------------------


def test_dat02_ingesting_twice_adds_no_duplicates(settings, store, embedder) -> None:
    sources = [make_source("a"), make_source("b")]
    write_raw(settings, sources[0], html_page(ENGLISH, MORE_ENGLISH))
    write_raw(settings, sources[1], html_page(MORE_ENGLISH + " Second document."))
    first = ingest_all(sources, settings.raw_dir, store, embedder, settings)
    second = ingest_all(sources, settings.raw_dir, store, embedder, settings)
    assert first.total_points == second.total_points > 0
    assert first.corpus_version == second.corpus_version
    assert sum(d.embedded for d in second.docs) == 0  # nothing re-embedded


def test_dat03_updated_document_replaces_old_chunks_and_changes_version(
    settings, store, embedder
) -> None:
    src = make_source("doc")
    write_raw(settings, src, html_page(ENGLISH))
    before = ingest_all([src], settings.raw_dir, store, embedder, settings)
    old_ids = store.ids_for_source("doc")
    write_raw(settings, src, html_page(MORE_ENGLISH))
    after = ingest_all([src], settings.raw_dir, store, embedder, settings)
    assert after.corpus_version != before.corpus_version
    assert after.docs[0].removed == len(old_ids)
    assert store.ids_for_source("doc").isdisjoint(old_ids)
    assert store.corpus_version() == after.corpus_version


def test_dat01_scanned_pdf_is_skipped_with_warning(settings, store, embedder) -> None:
    scanned, good = make_source("scanned", "pdf"), make_source("good")
    write_raw(settings, scanned, blank_pdf())
    write_raw(settings, good, html_page(ENGLISH))
    report = ingest_all([scanned, good], settings.raw_dir, store, embedder, settings)
    doc = {d.source_id: d for d in report.docs}
    assert (
        doc["scanned"].status == "skipped" and "no extractable text" in doc["scanned"].warnings[0]
    )
    assert store.ids_for_source("scanned") == set()
    assert doc["good"].status == "ingested"


def test_dat06_non_english_document_is_flagged_not_embedded(settings, store, embedder) -> None:
    src = make_source("francais")
    write_raw(settings, src, html_page(FRENCH, FRENCH.replace("levier", "marge")))
    report = ingest_all([src], settings.raw_dir, store, embedder, settings)
    assert report.docs[0].non_english > 0
    assert store.ids_for_source("francais") == set()


def test_dat07_missing_and_corrupt_files_do_not_stop_ingestion(settings, store, embedder) -> None:
    missing, corrupt, good = (
        make_source("missing"),
        make_source("corrupt", "pdf"),
        make_source("good"),
    )
    write_raw(settings, corrupt, b"%PDF-1.4 this is not really a pdf")
    write_raw(settings, good, html_page(ENGLISH))
    report = ingest_all([missing, corrupt, good], settings.raw_dir, store, embedder, settings)
    status = {d.source_id: d.status for d in report.docs}
    assert status == {"missing": "missing", "corrupt": "failed", "good": "ingested"}


def test_documents_removed_from_registry_are_removed_from_store(settings, store, embedder) -> None:
    a, b = make_source("a"), make_source("b")
    write_raw(settings, a, html_page(ENGLISH))
    write_raw(settings, b, html_page(MORE_ENGLISH))
    ingest_all([a, b], settings.raw_dir, store, embedder, settings)
    report = ingest_all([a], settings.raw_dir, store, embedder, settings)
    assert report.removed_unlisted > 0 and store.ids_for_source("b") == set()


def test_dat04_embedding_happens_in_bounded_batches(settings, store) -> None:
    class CountingEmbedder(HashEmbedder):
        biggest = 0

        def embed_documents(self, texts):
            CountingEmbedder.biggest = max(CountingEmbedder.biggest, len(texts))
            return super().embed_documents(texts)

    embedder = CountingEmbedder(dim=128)
    store = VectorStore(QdrantClient(":memory:"), "batches", embedder)
    src = make_source("long")
    write_raw(settings, src, html_page(*[f"Section {i}. {ENGLISH}" for i in range(40)]))
    report = ingest_all([src], settings.raw_dir, store, embedder, settings)
    assert report.docs[0].chunks > settings.embed_batch_size
    assert CountingEmbedder.biggest <= settings.embed_batch_size


# --- Embedding model mismatch (DAT-10) ---------------------------------------------------


def test_dat10_mismatched_embedding_model_is_refused() -> None:
    client = QdrantClient(":memory:")
    VectorStore(client, "docs", HashEmbedder(dim=64, model_name="model-a")).create()
    other = VectorStore(client, "docs", HashEmbedder(dim=32, model_name="model-b"))
    with pytest.raises(EmbeddingMismatchError) as err:
        other.check_compatible()
    message = str(err.value)
    assert "model-a" in message and "model-b" in message and "--rebuild" in message


def test_dat10_missing_collection_tells_you_to_ingest() -> None:
    store = VectorStore(QdrantClient(":memory:"), "nothing", HashEmbedder())
    with pytest.raises(CollectionMissingError, match="make ingest"):
        store.check_compatible()
