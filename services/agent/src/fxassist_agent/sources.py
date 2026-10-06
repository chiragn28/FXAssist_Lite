"""The source registry: data/sources.yaml is the single list of documents in the corpus.

data/SOURCES.md is generated from it (`make sources-md`), so the two never drift (DAT-08).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

Format = Literal["html", "pdf", "wikipedia"]
FORMATS: tuple[str, ...] = ("html", "pdf", "wikipedia")
REQUIRED = (
    "id",
    "title",
    "publisher",
    "url",
    "format",
    "licence",
    "licence_url",
    "redistributable",
)


class RegistryError(ValueError):
    """data/sources.yaml is malformed."""


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    publisher: str
    url: str  # canonical, human-readable URL used in citations
    format: Format
    licence: str
    licence_url: str
    redistributable: bool
    fetch_url: str | None = None  # where to download from, if different from url
    published: str | None = None
    notes: str | None = None

    @property
    def download_url(self) -> str:
        return self.fetch_url or self.url

    @property
    def extension(self) -> str:
        return {"html": "html", "pdf": "pdf", "wikipedia": "json"}[self.format]


def load_sources(path: Path) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise RegistryError(f"{path}: expected a mapping with a 'sources' list")
    sources: list[Source] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw["sources"]):
        missing = [k for k in REQUIRED if k not in entry]
        if missing:
            raise RegistryError(f"{path}: source #{i + 1} is missing {missing}")
        if entry["format"] not in FORMATS:
            raise RegistryError(f"{path}: {entry['id']}: format must be one of {FORMATS}")
        if entry["id"] in seen:
            raise RegistryError(f"{path}: duplicate id {entry['id']}")
        if not isinstance(entry["redistributable"], bool):
            raise RegistryError(f"{path}: {entry['id']}: redistributable must be true or false")
        seen.add(entry["id"])
        known = {f for f in Source.__dataclass_fields__}
        sources.append(Source(**{k: v for k, v in entry.items() if k in known}))
    return sources


def render_sources_md(sources: list[Source], excluded: list[dict], fetched: dict[str, str]) -> str:
    """Render data/SOURCES.md. `fetched` maps source id to the date it was last downloaded."""
    lines = [
        "# Document sources",
        "",
        "<!-- Generated from data/sources.yaml by `make sources-md`. Do not edit by hand. -->",
        "",
        "Every document in the corpus: where it came from, its licence, when it was fetched, and",
        "whether it may be redistributed. Raw files are never committed (`data/raw/` is git-ignored);",
        "only the URL and the fetch code are (DAT-08, ADR-001). Documents that are not",
        "redistributable are used locally for personal study only and must never be deployed publicly.",
        "",
        f"**{len(sources)} documents**, {sum(s.redistributable for s in sources)} redistributable.",
        "",
        "| ID | Title | Publisher | Format | Licence / terms | Redistributable | Fetched |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in sources:
        lines.append(
            f"| `{s.id}` | [{s.title}]({s.url}) | {s.publisher} | {s.format} | "
            f"[{s.licence}]({s.licence_url}) | {'yes' if s.redistributable else '**no**'} | "
            f"{fetched.get(s.id, 'not yet')} |"
        )
    if excluded:
        lines += ["", "## Considered and excluded", "", "| Source | Reason |", "|---|---|"]
        lines += [f"| [{e['title']}]({e['url']}) | {e['reason']} |" for e in excluded]
    lines += [
        "",
        "## Attribution",
        "",
        "- CFTC material: public domain; courtesy of the U.S. Commodity Futures Trading Commission.",
        "- ESMA material: source: European Securities and Markets Authority (esma.europa.eu). "
        "ESMA does not endorse this project.",
        "- ASIC material: © Australian Securities & Investments Commission, CC BY 4.0.",
        "- Wikipedia articles: CC BY-SA 4.0; authors listed in each article's history. The "
        "exact revision used is recorded in the fetch metadata.",
        "- FCA material: © Financial Conduct Authority. Downloaded for personal use under the "
        "FCA legal terms (clause 3.2); not redistributed.",
        "",
    ]
    return "\n".join(lines)


def load_excluded(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(raw.get("excluded", [])) if isinstance(raw, dict) else []
