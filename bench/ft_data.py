"""Fine-tuning data from the real agent, with a larger teacher model (ADR-026).

Runs in the GPU lab's eval virtualenv (it needs fxassist_agent), against an indexed corpus and a
teacher model behind the usual OpenAI-compatible endpoint (FXA_LLM_BASE_URL, FXA_LLM_MODEL):

  1. sample chunks from the vector store and have the teacher write one question per chunk
  2. drop questions too close to any evaluation question (leakage, FT-01) or to each other
  3. ask every question through the real agent with the teacher as its LLM, recording each
     model call (grader, rewrite, answer)
  4. keep only runs whose final answer passed the agent's own citation and number checks
     (FT-02): the validator is the quality filter, no paid judge
  5. add "the excerpts do not answer this" examples, so the student does not learn to always
     answer (FT-03)

Prompts are exactly the agent's, so the student is trained on what it will see at inference.

    python -m bench.ft_data --out DIR [--chunks 450] [--workers 24] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

QUESTION_SYSTEM = """You write questions for testing a question-answering assistant about forex and CFD trading.
Given one passage from a regulator's or an educational document, write ONE question that a retail trader might ask and that the passage answers completely.
- Make it self-contained: name the regulator, country or product when the passage is specific to one.
- Do not mention "the passage", "the document" or "the text".
- Do not ask for personal advice, recommendations or predictions.
If the passage has no useful fact (a table of contents, a list of names, boilerplate), reply {"question": null}.
Reply with JSON only: {"question": "..."}"""

LEAK_SIMILARITY = 0.80  # cosine to any evaluation question: dropped (FT-01)
DUPLICATE_SIMILARITY = 0.92  # cosine between generated questions: keep the first
NEGATIVE_SHARE = 0.15  # "excerpts do not answer" examples, as a share of answered ones
MIN_CHUNK_CHARS = 400

_QUESTION_TAG = re.compile(r"<question>\n.*?\n</question>", re.S)


# --- Pure helpers (tested offline in bench/tests) ------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def filter_questions(
    questions: list[str],
    vectors: list[list[float]],
    eval_vectors: list[list[float]],
    *,
    leak: float = LEAK_SIMILARITY,
    duplicate: float = DUPLICATE_SIMILARITY,
) -> tuple[list[int], Counter]:
    """Indexes of questions to keep, and why the others were dropped (FT-01)."""
    kept: list[int] = []
    why: Counter = Counter()
    for i, v in enumerate(vectors):
        if not questions[i] or len(questions[i]) < 12:
            why["empty"] += 1
        elif eval_vectors and max(cosine(v, e) for e in eval_vectors) >= leak:
            why["too close to an eval question"] += 1
        elif any(cosine(v, vectors[j]) >= duplicate for j in kept):
            why["duplicate"] += 1
        else:
            kept.append(i)
    return kept, why


def parse_question(raw: str) -> str | None:
    """The teacher's {"question": ...}, tolerating code fences and stray text."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0)).get("question")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def is_answer_call(messages: list[dict[str, str]], system_prompt: str) -> bool:
    return bool(messages) and messages[0]["content"].startswith(system_prompt)


def negative_example(
    answer_messages: list[dict[str, str]], other_question: str, insufficient: str
) -> list[dict[str, str]]:
    """Excerpts retrieved for one question, asked a different one: the answer is "no answer"."""
    user = answer_messages[1]["content"]
    swapped = _QUESTION_TAG.sub(
        lambda _: f"<question>\n{other_question}\n</question>", user, count=1
    )
    if swapped == user:
        raise ValueError("answer prompt has no <question> block")
    return [
        answer_messages[0],
        {"role": "user", "content": swapped},
        {"role": "assistant", "content": insufficient},
    ]


# --- Recording LLM ---------------------------------------------------------------------------


@dataclass
class RecordingLLM:
    """Wraps the teacher; remembers each call of one agent run."""

    inner: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, messages, *, max_tokens, temperature=0.0, json_mode=False, **kw):
        text = self.inner.complete(
            messages, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode, **kw
        )
        self.calls.append({"messages": messages, "response": text})
        return text

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class _Locked:
    """Serialises access to an object that is not safe to share across threads (local Qdrant)."""

    def __init__(self, inner: Any):
        self._inner, self._lock = inner, threading.Lock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)

        return call


# --- Pipeline --------------------------------------------------------------------------------


def _scroll_chunks(store) -> list[dict[str, Any]]:
    chunks, offset = [], None
    while True:
        points, offset = store.client.scroll(
            store.collection, limit=500, offset=offset, with_payload=True, with_vectors=False
        )
        chunks += [p.payload or {} for p in points]
        if offset is None:
            return chunks


def _eval_questions(eval_dir: Path) -> list[str]:
    out = []
    for name, key in (("questions.yaml", "questions"), ("attacks.yaml", "attacks")):
        path = eval_dir / name
        if path.exists():
            out += [q["question"] for q in yaml.safe_load(path.read_text())[key]]
    return out


def main(argv: list[str] | None = None) -> int:
    from fxassist_agent import prompts
    from fxassist_agent.cli import build_embedder, build_llm, build_store
    from fxassist_agent.config import Settings
    from fxassist_agent.graph import Agent, AgentDeps

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--chunks", type=int, default=450)
    p.add_argument("--workers", type=int, default=24)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)  # noqa: S311 - reproducible sampling, not security
    started = time.monotonic()
    report: dict[str, Any] = {"seed": args.seed}

    settings = Settings()
    embedder = build_embedder(settings)
    store = build_store(settings, embedder)
    store.check_compatible()
    teacher = build_llm(settings)
    teacher.check_model()
    report["teacher"] = settings.llm_model
    report["corpus_version"] = store.corpus_version()

    # 1. Questions from sampled chunks
    chunks = [c for c in _scroll_chunks(store) if len(c.get("text", "")) >= MIN_CHUNK_CHARS]
    rng.shuffle(chunks)
    chunks = chunks[: args.chunks]
    report["chunks_sampled"] = len(chunks)

    def ask_for_question(chunk: dict[str, Any]) -> str | None:
        messages = [
            {"role": "system", "content": QUESTION_SYSTEM},
            {
                "role": "user",
                "content": f"Source: {chunk.get('publisher')}: {chunk.get('title')}\n\n"
                + prompts.neutralise(chunk["text"]),
            },
        ]
        try:
            return parse_question(teacher.complete(messages, max_tokens=80, json_mode=True))
        except Exception:  # one failed call costs one question, not the run
            return None

    with ThreadPoolExecutor(args.workers) as pool:
        raw_questions = list(pool.map(ask_for_question, chunks))
    pairs = [(q, c) for q, c in zip(raw_questions, chunks, strict=True) if q]
    report["questions_generated"] = len(pairs)

    # 2. Leakage and duplicate filter
    questions = [q for q, _ in pairs]
    vectors = embedder.embed_documents(questions) if questions else []
    eval_vectors = embedder.embed_documents(_eval_questions(settings.eval_dir))
    kept, dropped = filter_questions(questions, vectors, eval_vectors)
    report["questions_dropped"] = dict(dropped)
    pairs = [pairs[i] for i in kept]
    report["questions_kept"] = len(pairs)

    # 3. The real agent, with the teacher as its LLM
    shared_store, shared_embedder = _Locked(store), _Locked(embedder)

    def run_agent(question: str) -> dict[str, Any]:
        llm = RecordingLLM(teacher)
        agent = Agent(
            AgentDeps(settings=settings, store=shared_store, embedder=shared_embedder, llm=llm)
        )
        try:
            result = agent.ask(question)
        except Exception as exc:  # recorded, not fatal
            return {"question": question, "outcome": "error", "reason": str(exc), "calls": []}
        return {
            "question": question,
            "outcome": result.outcome,
            "answer": result.answer,
            "reason": result.reason,
            "sources": sorted({c.source_id for c in result.citations}),
            "calls": llm.calls,
        }

    with ThreadPoolExecutor(args.workers) as pool:
        runs = list(pool.map(run_agent, [q for q, _ in pairs]))
    report["agent_outcomes"] = dict(Counter(r["outcome"] for r in runs))

    # 4. Keep validated runs only; the answer target is the validated (cleaned) answer
    examples: list[dict[str, Any]] = []
    answered = [r for r in runs if r["outcome"] == "answered"]
    for r in answered:
        for call in r["calls"]:
            if is_answer_call(call["messages"], prompts.SYSTEM_PROMPT):
                kind, target = "answer", r["answer"]
            elif call["messages"][0]["content"] == prompts.GRADER_SYSTEM:
                kind, target = "grade", call["response"]
            else:
                kind, target = "rewrite", call["response"]
            examples.append(
                {
                    "kind": kind,
                    "question": r["question"],
                    "messages": [
                        *call["messages"],
                        {"role": "assistant", "content": target.strip()},
                    ],
                }
            )

    # 5. Negatives: another question's excerpts, from different sources, dissimilar question
    answer_calls = {e["question"]: e["messages"][:2] for e in examples if e["kind"] == "answer"}
    vec_of = dict(zip(questions, vectors, strict=True))
    sources_of = {r["question"]: set(r["sources"]) for r in answered}
    wanted = int(len(answer_calls) * NEGATIVE_SHARE)
    pool_q = list(answer_calls)
    attempts = 0
    negatives = 0
    while negatives < wanted and attempts < wanted * 20 and len(pool_q) > 1:
        attempts += 1
        a, b = rng.sample(pool_q, 2)
        if sources_of[a] & sources_of[b] or cosine(vec_of[a], vec_of[b]) >= 0.5:
            continue
        examples.append(
            {
                "kind": "negative",
                "question": b,
                "messages": negative_example(answer_calls[a], b, prompts.INSUFFICIENT),
            }
        )
        negatives += 1

    # Split by question, so a question's grader and answer examples land on the same side
    rng.shuffle(examples)
    val_questions = set(rng.sample(sorted(answer_calls), max(1, len(answer_calls) // 20)))
    train = [e for e in examples if e["question"] not in val_questions]
    val = [e for e in examples if e["question"] in val_questions]
    for name, rows in (("train.jsonl", train), ("val.jsonl", val)):
        with (args.out / name).open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
    with (args.out / "agent-runs.jsonl").open("w") as f:
        for r in runs:
            f.write(json.dumps({k: v for k, v in r.items() if k != "calls"}) + "\n")

    report |= {
        "examples": dict(Counter(e["kind"] for e in examples)),
        "train_examples": len(train),
        "val_examples": len(val),
        "minutes": round((time.monotonic() - started) / 60, 1),
    }
    (args.out / "data-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if train else 1


if __name__ == "__main__":
    raise SystemExit(main())
