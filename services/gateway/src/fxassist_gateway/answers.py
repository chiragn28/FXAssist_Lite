"""Cache keys, cache lifetimes and the response payload.

Cache key (ADR-010): SHA-256 of [normalised question, corpus version, prompt version, model].
  - CAC-02: a new corpus (re-ingest), prompt edit or model switch changes the key, so stale
    answers are never served; old entries simply expire.
  - CAC-04: the only user-controlled input is the question, and it goes in only through the
    hash. Nothing a caller sends can choose, extend or collide with another entry's key.
    Answers do not depend on who asks, so the API key is not part of it.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from fxassist_agent.graph import AgentResult

from .config import DISCLAIMER

CACHE_NAMESPACE = "fxa:answer:v1:"
_SPACE = re.compile(r"\s+")


def normalise_question(question: str) -> str:
    """Same meaning, same key: Unicode NFKC, case-folded, whitespace collapsed, trailing
    punctuation dropped ("What is a pip?" == "what is a  pip")."""
    text = unicodedata.normalize("NFKC", question).casefold()
    return _SPACE.sub(" ", text).strip().rstrip("?!. ")


def cache_key(question: str, corpus_version: str, prompt_version: str, model_id: str) -> str:
    material = json.dumps(
        [normalise_question(question), corpus_version, prompt_version, model_id],
        ensure_ascii=False,
    )
    return CACHE_NAMESPACE + hashlib.sha256(material.encode()).hexdigest()


def ttl_for(outcome: str, *, normal_s: int, short_s: int) -> int:
    """CAC-03: errors are never cached; abstentions only briefly (the next ingest may add the
    answer); guard decisions are not cached because they cost nothing to recompute."""
    if outcome in ("answered", "declined_advice"):
        return normal_s
    if outcome in ("abstained", "out_of_scope"):
        return short_s
    return 0


def payload(result: AgentResult, *, corpus_version: str | None, model: str) -> dict[str, Any]:
    """The part of a response that can be cached and shared between identical requests."""
    return {
        "outcome": result.outcome,
        "answer": result.answer,
        "citations": [
            {
                "label": c.label,
                "title": c.title,
                "publisher": c.publisher,
                "url": c.url,
                "page": c.page,
            }
            for c in result.citations
        ],
        "disclaimer": DISCLAIMER,  # SAF-06: from the gateway, on every answer
        "corpus_version": corpus_version,
        "model": model,
    }
