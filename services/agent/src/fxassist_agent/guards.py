"""Deterministic checks on the user's question, run before any retrieval or model call.

Rules, not a model, decide these cases: they are predictable, cheap and testable, and a small
model can be talked out of its instructions. The prompt (prompts.py) is the second line of
defence and the citation validator (citations.py) the third.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["ok", "empty", "too_long", "vague", "advice", "reveal", "injection"]


@dataclass(frozen=True)
class GuardResult:
    kind: Kind
    reason: str = ""


def _any(patterns: list[str]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


# SAF-04: attempts to override the assistant's instructions.
_INJECTION = _any(
    [
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|your|the|system)\b.{0,20}\b(instructions?|rules|prompts?|guidelines|directives)",
        r"\byou are (now|no longer)\b",
        r"\b(act|behave|respond) as (if|an?|the)\b",
        r"\bpretend (to be|you are)\b",
        r"\b(developer|god|jailbreak|dan) mode\b",
        r"\bjailbreak\b",
        r"^\s*(system|assistant)\s*:",
        r"\bnew (instructions|rules)\s*:",
        r"</?(excerpts?|question|system)>",
    ]
)

# SAF-05: requests to reveal the prompt, configuration or secrets.
_REVEAL = _any(
    [
        r"\b(show|reveal|print|repeat|display|output|leak|dump|give|tell|share|what('s| is| are))\b.{0,40}\b(system prompt|your (instructions|prompt|rules|configuration|guidelines)|initial prompt|hidden prompt|developer message)",
        r"\b(api[ _-]?keys?|secret keys?|access tokens?|passwords?|credentials|env(ironment)? variables?)\b",
    ]
)

# SAF-01 / SAF-02: personal trading or investment advice, predictions, guaranteed returns.
_ADVICE = _any(
    [
        r"\bshould i\b.{0,30}\b(buy|sell|short|long|trade|invest|open|close|enter|exit|hold)\b",
        r"\b(is|would) (it|now|this|today)\b.{0,20}\b(good|right|best|smart)\b.{0,20}\b(time|idea|moment)\b.{0,20}\b(buy|sell|trade|invest)",
        r"\bwhat (should|would) (i|you)\b.{0,30}\b(buy|sell|trade|invest)\b",
        r"\b(recommend|suggest|give me|send me)\b.{0,40}\b(trades?|pairs?|positions?|stocks?|currenc(y|ies)|entry|signals?)\b",
        r"\b(guarantee[ds]?|risk[- ]free|no[- ]risk|sure[- ]fire|can'?t lose)\b.{0,40}\b(returns?|profits?|income|gains?|money|win)",
        r"\b(returns?|profits?|income|gains?)\b.{0,30}\bguarantee[ds]?\b",
        # Not "trading signals" on its own: "What is a trading signal?" is a fair question.
        r"\b(profit|money[- ]making) (tips?|secrets?)\b",
        r"\bstrateg(y|ies) that (always|never) (wins?|loses?|profits?)\b",
        r"\bwill\b.{0,30}\b(eur|usd|gbp|jpy|aud|cad|chf|nzd|dollar|euro|pound|yen|price|rate|market)\b.{0,30}\b(go up|go down|rise|fall|drop|increase|decrease|crash|rally)\b",
        r"\b(predict|forecast)\b.{0,30}\b(price|rate|eur|usd|gbp|jpy|pair|market)\b",
    ]
)

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "what",
        "whats",
        "which",
        "who",
        "how",
        "why",
        "when",
        "where",
        "can",
        "could",
        "would",
        "should",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "me",
        "my",
        "i",
        "you",
        "your",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "about",
        "from",
        "by",
        "and",
        "or",
        "but",
        "so",
        "if",
        "then",
        "than",
        "please",
        "tell",
        "explain",
        "know",
        "want",
        "need",
        "give",
        "show",
        "some",
        "any",
        "there",
        "here",
        "more",
        "much",
        "many",
        "just",
    ]
)
# Words too generic to identify a topic on their own (RET-04).
_GENERIC = frozenset(
    [
        "forex",
        "fx",
        "cfd",
        "cfds",
        "trading",
        "trade",
        "trades",
        "trader",
        "traders",
        "help",
        "info",
        "information",
        "question",
        "questions",
        "something",
        "anything",
        "stuff",
        "thing",
        "things",
        "market",
        "markets",
        "topic",
        "it",
        "this",
        "that",
        "work",
        "works",
        "general",
        "basics",
        "overview",
    ]
)


def check_question(question: str, *, max_chars: int) -> GuardResult:
    text = question.strip()
    if not text:
        return GuardResult("empty", "empty question")
    if len(text) > max_chars:
        return GuardResult("too_long", f"question longer than {max_chars} characters")
    if _INJECTION.search(text):
        return GuardResult("injection", "attempt to override instructions")
    if _REVEAL.search(text):
        return GuardResult("reveal", "request for system prompt or secrets")
    if _ADVICE.search(text):
        return GuardResult("advice", "personal trading or investment advice requested")
    words = re.findall(r"[a-z0-9:/.%-]+", text.lower())
    content = [w for w in words if w not in _STOPWORDS and w not in _GENERIC and len(w) > 1]
    if not content:
        return GuardResult("vague", "no specific topic in the question")
    return GuardResult("ok")
