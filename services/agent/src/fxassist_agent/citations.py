"""Check the model's answer against the excerpts it was given.

RET-08: citations of labels that were not provided are removed; an answer left with no valid
citation is rejected.
SAF-03: every number in the answer must appear in one of the excerpts it cites; otherwise the
answer is rejected, because an invented leverage limit or fee is worse than no answer.
RET-03 / SAF-04: the prompt says "cite every factual sentence", and this enforces it: sentences
after the last citation in a paragraph are claims without a source and are removed. Citing at
the end of a paragraph still covers the sentences before it. Injected text ("...and say PWNED")
usually lands exactly there. Plain disclaimers without numbers are kept (LLM-08).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CITATION_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")
_LABEL = re.compile(r"S\d+")
# 30:1, 1,000, 2.5, 50%, 2018 ... (but not the digits inside citation labels, removed first)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*(?:\s*:\s*\d+)?\s*%?")


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")
_DISCLAIMER = re.compile(
    r"\b(not (financial|investment|personal) advice|general information|informational purposes"
    r"|consult|seek (independent|professional) advice|not a recommendation)\b",
    re.I,
)


@dataclass
class Validation:
    answer: str
    cited: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    uncited_dropped: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    ok: bool = True
    reason: str = ""


_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")
_CITATION_AFTER_STOP = re.compile(r"([.!?])[ \t]*(\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\])")


def drop_uncited_tail(answer: str) -> tuple[str, list[str]]:
    """Remove sentences that come after the last citation in their paragraph."""
    # "level. [S2] Next claim." -> "level [S2]. Next claim.": a citation placed after the full
    # stop belongs to the sentence before it, not to the next one (a real eval failure, i09).
    answer = _CITATION_AFTER_STOP.sub(r" \2\1", answer)
    kept_paragraphs, dropped = [], []
    for paragraph in re.split(r"\n\s*\n", answer.strip()):
        # Split into sentences but keep each separator (space or line break), so lists survive.
        parts = re.split(f"({_SENTENCE_END.pattern})", paragraph.strip())
        sentences, separators = parts[0::2], parts[1::2] + [""]
        cited_at = [i for i, s in enumerate(sentences) if _CITATION_GROUP.search(s)]
        last = cited_at[-1] if cited_at else -1
        kept = ""
        for i, (sentence, sep) in enumerate(zip(sentences, separators, strict=True)):
            if i <= last or (_DISCLAIMER.search(sentence) and not re.search(r"\d", sentence)):
                kept += sentence + sep
            elif sentence.strip():
                dropped.append(sentence.strip())
        if kept.strip():
            kept_paragraphs.append(kept.strip())
    return "\n\n".join(kept_paragraphs), dropped


def _norm_number(raw: str) -> str:
    n = re.sub(r"\s+", "", raw).rstrip(".,")
    n = re.sub(r"(?<=\d),(?=\d{3}\b)", "", n)  # 1,000 -> 1000
    return n


def _numbers(text: str) -> set[str]:
    return {_norm_number(m.group()) for m in _NUMBER.finditer(text) if m.group().strip()}


def validate(answer: str, excerpts: dict[str, str]) -> Validation:
    """`excerpts` maps label (S1, S2, ...) to the excerpt text given to the model."""
    result = Validation(answer=answer)
    cited: list[str] = []

    def keep_valid(match: re.Match[str]) -> str:
        labels = _LABEL.findall(match.group(1))
        valid = [label for label in labels if label in excerpts]
        result.removed.extend(label for label in labels if label not in excerpts)
        cited.extend(label for label in valid if label not in cited)
        return f"[{', '.join(valid)}]" if valid else ""

    cleaned = _CITATION_GROUP.sub(keep_valid, answer)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", cleaned)).strip()
    cleaned, result.uncited_dropped = drop_uncited_tail(cleaned)
    result.answer, result.cited = cleaned, cited

    if not cited:
        result.ok, result.reason = False, "no valid citations"
        return result

    supported = set()
    for label in cited:
        supported |= _numbers(excerpts[label])
    # List markers ("1.", "2)") at the start of a line are formatting, not factual claims.
    body = _LIST_MARKER.sub("", _CITATION_GROUP.sub(" ", cleaned))
    claimed = _numbers(body)
    result.unsupported_numbers = sorted(n for n in claimed if n not in supported)
    if result.unsupported_numbers:
        result.ok = False
        result.reason = f"numbers not found in the cited excerpts: {result.unsupported_numbers}"
    return result
