"""Download the corpus into data/raw (git-ignored, DAT-08).

DAT-07: one failing URL never stops the run; every failure is listed in the report, and a
file only appears under its final name once it has downloaded completely (atomic rename),
so a crash or a 404 never leaves a partial document behind.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .sources import Source

log = logging.getLogger(__name__)

# Wikimedia's User-Agent policy asks automated clients for contact details (a URL or e-mail).
# On Kaggle (2026-10-07) all 10 Wikipedia documents were missing from the index while the same
# downloads worked from a home connection; a missing contact URL is the likely cause (the fetch
# report from that run was not kept, so the HTTP status is unknown).
USER_AGENT = (
    "FXAssistLite/0.1 (+https://github.com/chiragn28/FXAssist_Lite; educational RAG project)"
)
MAX_BYTES = 25 * 1024 * 1024  # refuse anything larger than 25 MB


@dataclass
class FetchResult:
    source_id: str
    status: str  # downloaded | cached | failed
    path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    error: str | None = None


@dataclass
class FetchReport:
    started_at: str
    results: list[FetchResult] = field(default_factory=list)

    @property
    def failed(self) -> list[FetchResult]:
        return [r for r in self.results if r.status == "failed"]

    def to_json(self) -> str:
        return json.dumps(
            {"started_at": self.started_at, "results": [asdict(r) for r in self.results]},
            indent=2,
        )


def raw_path(raw_dir: Path, source: Source) -> Path:
    return raw_dir / f"{source.id}.{source.extension}"


def meta_path(raw_dir: Path, source: Source) -> Path:
    return raw_dir / f"{source.id}.meta.json"


def make_client(timeout_s: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout_s,
        follow_redirects=True,
    )


def _validate(source: Source, content: bytes, content_type: str) -> None:
    if not content:
        raise ValueError("empty response body")
    if source.format == "pdf" and not content.startswith(b"%PDF"):
        raise ValueError(f"expected a PDF, got {content_type or 'unknown content type'}")
    if source.format == "wikipedia":
        pages = json.loads(content).get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            raise ValueError("Wikipedia page not found")


def _write_atomic(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def fetch_one(
    client: httpx.Client, source: Source, raw_dir: Path, *, retries: int = 2
) -> FetchResult:
    target = raw_path(raw_dir, source)
    last_error = "unknown error"
    for attempt in range(retries + 1):
        try:
            response = client.get(source.download_url)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                last_error = f"HTTP {response.status_code}"
                time.sleep(2**attempt)
                continue
            if response.status_code != 200:
                return FetchResult(source.id, "failed", error=f"HTTP {response.status_code}")
            content = response.content
            if len(content) > MAX_BYTES:
                return FetchResult(source.id, "failed", error=f"larger than {MAX_BYTES} bytes")
            _validate(source, content, response.headers.get("content-type", ""))
            _write_atomic(target, content)
            digest = hashlib.sha256(content).hexdigest()
            meta = {
                "source_id": source.id,
                "url": source.url,
                "download_url": source.download_url,
                "final_url": str(response.url),
                "content_type": response.headers.get("content-type"),
                "sha256": digest,
                "bytes": len(content),
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            if source.format == "wikipedia":
                page = json.loads(content)["query"]["pages"][0]
                meta["wikipedia_revision"] = page["revisions"][0]["revid"]
            _write_atomic(meta_path(raw_dir, source), json.dumps(meta, indent=2).encode())
            return FetchResult(source.id, "downloaded", str(target), digest, len(content))
        except (httpx.HTTPError, ValueError, json.JSONDecodeError, KeyError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries and isinstance(exc, httpx.TransportError):
                time.sleep(2**attempt)
                continue
            break
    return FetchResult(source.id, "failed", error=last_error)


def fetch_all(
    sources: list[Source],
    raw_dir: Path,
    *,
    client: httpx.Client | None = None,
    force: bool = False,
) -> FetchReport:
    report = FetchReport(started_at=datetime.now(UTC).isoformat(timespec="seconds"))
    own_client = client is None
    client = client or make_client()
    try:
        for source in sources:
            target = raw_path(raw_dir, source)
            if target.exists() and meta_path(raw_dir, source).exists() and not force:
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                report.results.append(
                    FetchResult(source.id, "cached", str(target), digest, target.stat().st_size)
                )
                continue
            result = fetch_one(client, source, raw_dir)
            if result.status == "failed":
                log.warning("fetch failed for %s: %s", source.id, result.error)
            report.results.append(result)
    finally:
        if own_client:
            client.close()
    return report


def fetched_dates(raw_dir: Path, sources: list[Source]) -> dict[str, str]:
    """Date each source was last downloaded, from its metadata sidecar."""
    dates: dict[str, str] = {}
    for source in sources:
        path = meta_path(raw_dir, source)
        if path.exists():
            dates[source.id] = json.loads(path.read_text())["fetched_at"][:10]
    return dates
