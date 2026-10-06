"""Turn downloaded files into clean page text.

- PDFs are read one page at a time, so a 500-page document never sits in memory (DAT-04).
- Repeated headers/footers and page-number lines are removed (DAT-05).
- HTML keeps only the main content (navigation, menus and footers dropped by trafilatura).
- Wikipedia extracts drop the reference sections.
- `english_ratio` flags non-English text so it is never silently embedded (DAT-06).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import trafilatura
from pypdf import PdfReader

from .sources import Source


@dataclass(frozen=True)
class Page:
    number: int | None  # 1-based page number for PDFs, None for web pages
    text: str


# --- Normalisation --------------------------------------------------------------

_PAGE_NUMBER_LINE = re.compile(r"^\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.I)
_HYPHEN_BREAK = re.compile(r"(\w)-\n(?=[a-z])")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub(r"\1", text)  # "exam-\nple" -> "example"
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _line_key(line: str) -> str:
    """Compare header/footer lines while ignoring the page number inside them."""
    return re.sub(r"\d+", "#", line.strip().lower())


# --- PDF --------------------------------------------------------------------------


def learn_boilerplate(pages: list[str], *, edge_lines: int = 5, min_share: float = 0.5) -> set[str]:
    """Lines that open or close at least `min_share` of the pages are headers or footers.

    Five edge lines, because some running headers are tall (FCA: page number, document
    reference, chapter, publisher and title on separate lines).
    """
    if len(pages) < 3:
        return set()
    counts: Counter[str] = Counter()
    for text in pages:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        edges = {_line_key(ln) for ln in lines[:edge_lines] + lines[-edge_lines:]}
        counts.update(edges)
    threshold = max(3, int(len(pages) * min_share))
    return {key for key, n in counts.items() if n >= threshold and key.strip("# ")}


def strip_boilerplate(text: str, boilerplate: set[str]) -> str:
    kept = [
        ln
        for ln in text.splitlines()
        if not _PAGE_NUMBER_LINE.match(ln) and _line_key(ln) not in boilerplate
    ]
    return "\n".join(kept)


def iter_pdf_pages(path: Path, *, sample_pages: int = 30) -> Iterator[Page]:
    reader = PdfReader(path)
    total = len(reader.pages)
    sample = [reader.pages[i].extract_text() or "" for i in range(min(sample_pages, total))]
    boilerplate = learn_boilerplate(sample)
    for i in range(total):
        raw = sample[i] if i < len(sample) else (reader.pages[i].extract_text() or "")
        yield Page(i + 1, normalise(strip_boilerplate(raw, boilerplate)))


# --- HTML and Wikipedia -----------------------------------------------------------


def extract_html(html: str) -> str:
    text = trafilatura.extract(
        html,
        output_format="txt",
        include_tables=True,
        include_comments=False,
        include_links=False,
        favor_precision=True,
    )
    return normalise(text or "")


_WIKI_HEADING = re.compile(r"^(=+)\s*(.*?)\s*\1\s*$", re.M)
_WIKI_STOP_SECTIONS = {"see also", "references", "notes", "further reading", "external links"}


def extract_wikipedia(api_json: str) -> str:
    page = json.loads(api_json)["query"]["pages"][0]
    text = page.get("extract", "")
    for match in _WIKI_HEADING.finditer(text):
        if match.group(2).strip().lower() in _WIKI_STOP_SECTIONS:
            text = text[: match.start()]
            break
    text = _WIKI_HEADING.sub(lambda m: m.group(2), text)
    return normalise(text)


def iter_pages(source: Source, path: Path) -> Iterator[Page]:
    if source.format == "pdf":
        yield from iter_pdf_pages(path)
    elif source.format == "html":
        yield Page(None, extract_html(path.read_text(encoding="utf-8", errors="replace")))
    else:
        yield Page(None, extract_wikipedia(path.read_text(encoding="utf-8")))


# --- Language (DAT-06) ------------------------------------------------------------

_ENGLISH_STOPWORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "more",
        "most",
        "my",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "may",
        "must",
        "also",
    ]
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def english_ratio(text: str, *, min_words: int = 20) -> float | None:
    """Share of words that are common English function words, or None if too short to judge.

    English prose scores roughly 0.3 to 0.5; other languages written in Latin script score
    near 0, and non-Latin scripts score 0. Tables of numbers have too few words to judge.
    """
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) < min_words:
        return None
    return sum(w in _ENGLISH_STOPWORDS for w in words) / len(words)


def is_non_english(text: str, *, threshold: float = 0.1) -> bool:
    ratio = english_ratio(text)
    return ratio is not None and ratio < threshold
