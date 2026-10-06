"""Fixed, hashed prompt sets for benchmarks (ADR-017, BEN-04).

Two sets, built deterministically from files in this repository (no network, nothing whose
licence forbids copying):
  short  the evaluation questions alone (tens of tokens): measures decode-bound behaviour
  long   each question after about 1,500 tokens of context, the size of a real RAG prompt
         (5 excerpts of ~1,000 characters): measures prefill-bound behaviour

The context text is this project's own ARCHITECTURE.md, cut into excerpt-sized pieces, so it
reads like documentation without copying any third-party document. The SHA-256 of each set is
recorded in every result row: two runs with different hashes are not comparable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LONG_CONTEXT_CHARS = 4500  # about 1,500 tokens at ~3 characters per token


@dataclass(frozen=True)
class PromptSet:
    name: str
    prompts: list[str]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.prompts).encode()).hexdigest()


def _questions(root: Path) -> list[str]:
    data = yaml.safe_load((root / "eval" / "questions.yaml").read_text())
    items = data["questions"] if isinstance(data, dict) else data
    wanted = ("answerable", "ambiguous")
    return [q["question"] for q in items if q.get("category") in wanted]


def _context_blocks(root: Path, size: int) -> list[str]:
    text = " ".join((root / "ARCHITECTURE.md").read_text().split())
    return [text[i : i + size] for i in range(0, len(text) - size, size)]


def short_set(root: Path = ROOT) -> PromptSet:
    return PromptSet("short", _questions(root))


def long_set(root: Path = ROOT) -> PromptSet:
    questions = _questions(root)
    blocks = _context_blocks(root, LONG_CONTEXT_CHARS)
    prompts = [
        "Answer the question using only the context.\n\n"
        f"<context>\n{blocks[i % len(blocks)]}\n</context>\n\n<question>{q}</question>"
        for i, q in enumerate(questions)
    ]
    return PromptSet("long", prompts)


def prompt_sets(root: Path = ROOT) -> dict[str, PromptSet]:
    return {s.name: s for s in (short_set(root), long_set(root))}
