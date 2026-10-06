"""The RAG agent as an explicit LangGraph state machine (ADR-007).

    START -> guard --(blocked)--> END
               |
               v
           retrieve --(off-domain)--> END
               |  ^         \\--(nothing relevant)--> rewrite (at most once) --> retrieve
               v  |                                   \\--(already rewritten)--> abstain --> END
             grade --(nothing relevant)--> rewrite / abstain
               |
               v
           generate --(model says insufficient)--> END
               |
               v
           validate --> END   (citations and numbers checked; failures become abstentions)

Every node checks the request deadline, and the whole run has a step limit (RET-09).
"""

from __future__ import annotations

import json
import logging
import math
import operator
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Annotated, Any, Literal, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from . import prompts
from .citations import validate
from .config import Settings
from .control import RunControl
from .embeddings import Embedder
from .guards import check_question
from .llm import ChatLLM, LLMError
from .store import Hit, StoreUnavailableError, VectorStore
from .tracing import tracer

log = logging.getLogger(__name__)

Outcome = Literal[
    "answered", "abstained", "out_of_scope", "clarify", "declined_advice", "refused", "error"
]

MESSAGES: dict[str, str] = {
    "abstained": "I don't have enough information in my documents to answer that.",
    "out_of_scope": (
        "That's outside what I can help with. I answer questions about forex and CFD trading "
        "using public regulatory and educational documents."
    ),
    "clarify": (
        'Could you be more specific? For example: "What is a margin call?" or '
        '"What leverage limits apply to retail CFD clients in the EU?"'
    ),
    "advice": (
        "I can't give personal trading or investment advice, recommendations or price "
        "predictions, and no one can guarantee returns from trading."
    ),
    "injection": (
        "I can't follow instructions that try to change how I work. Please ask your question "
        "about forex or CFDs directly."
    ),
    "reveal": "I can't share my instructions, configuration or any keys.",
    "too_long": "Your question is too long. Please shorten it and ask again.",
    "error": "Sorry, I couldn't produce an answer right now. Please try again shortly.",
}

ADVICE_QUERY = "risk warnings losses leverage margin retail clients CFD forex trading"


@dataclass(frozen=True)
class Citation:
    label: str
    source_id: str
    title: str
    publisher: str
    url: str
    page: int | None


@dataclass
class AgentResult:
    question: str
    outcome: Outcome
    answer: str
    citations: list[Citation] = field(default_factory=list)
    reason: str = ""
    retrieved: list[dict] = field(default_factory=list)  # first retrieval, for evaluation
    steps: list[dict] = field(default_factory=list)
    truncated: int = 0
    grader_errors: int = 0
    seconds: float = 0.0
    corpus_version: str | None = None
    rejected_answer: str | None = None
    error_code: str | None = None  # set when outcome == "error"; the gateway maps it to HTTP

    def to_dict(self) -> dict:
        return asdict(self)


class AgentState(TypedDict, total=False):
    question: str
    query: str
    advice: bool
    deadline: float
    rewrites: int
    retrieved: list[dict]
    hits: list[Hit]
    relevant: list[Hit]
    context: list[Hit]
    answer: str
    citations: list[Citation]
    outcome: str
    reason: str
    message_key: str
    error_code: str
    truncated: int
    grader_errors: int
    rejected_answer: str
    steps: Annotated[list[dict], operator.add]


@dataclass
class AgentDeps:
    settings: Settings
    store: VectorStore
    embedder: Embedder
    llm: ChatLLM
    clock: Callable[[], float] = time.monotonic
    control: RunControl | None = None  # cancellation and progress for one request (LLM-05)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def dedupe_hits(hits: list[Hit], threshold: float) -> list[Hit]:
    """RET-10: drop chunks nearly identical to a higher-ranked one (hits arrive best first)."""
    kept: list[Hit] = []
    for hit in hits:
        duplicate = any(
            (hit.vector and k.vector and _cosine(hit.vector, k.vector) >= threshold)
            or hit.text.strip() == k.text.strip()
            for k in kept
        )
        if not duplicate:
            kept.append(hit)
    return kept


def diversify(hits: list[Hit], max_per_source: int) -> list[Hit]:
    """Keep at most `max_per_source` hits per document, preserving rank order.

    Without this, one long document (e.g. a 50-page policy statement) can fill every slot
    and push out a shorter document that holds the actual answer.
    """
    if max_per_source <= 0:
        return hits
    counts: dict[str, int] = {}
    kept = []
    for hit in hits:
        if counts.get(hit.source_id, 0) < max_per_source:
            kept.append(hit)
            counts[hit.source_id] = counts.get(hit.source_id, 0) + 1
    return kept


def select_hits(
    raw: list[Hit], *, top_k: int, max_per_source: int, duplicate_similarity: float
) -> list[Hit]:
    """Ranked search results -> the excerpts the agent will consider (RET-10 + diversity)."""
    return diversify(dedupe_hits(raw, duplicate_similarity), max_per_source)[:top_k]


def _label(hits: list[Hit]) -> list[prompts.Excerpt]:
    return [
        prompts.Excerpt(f"S{i}", h.source_id, h.title, h.publisher, h.page, h.text)
        for i, h in enumerate(hits, start=1)
    ]


class Agent:
    def __init__(self, deps: AgentDeps):
        self.deps = deps
        self.s = deps.settings
        self.graph = self._build()

    # --- Graph wiring ------------------------------------------------------------

    def _build(self):
        g = StateGraph(AgentState)
        for name, fn in [
            ("guard", self._guard),
            ("retrieve", self._retrieve),
            ("grade", self._grade),
            ("rewrite", self._rewrite),
            ("abstain", self._abstain),
            ("generate", self._generate),
            ("validate", self._validate),
        ]:
            g.add_node(name, self._wrap(name, fn))
        g.add_edge(START, "guard")
        g.add_conditional_edges("guard", self._stop_or("retrieve"), ["retrieve", END])
        g.add_conditional_edges(
            "retrieve", self._after_retrieve, ["grade", "generate", "rewrite", "abstain", END]
        )
        g.add_conditional_edges("grade", self._after_grade, ["generate", "rewrite", "abstain", END])
        g.add_conditional_edges("rewrite", self._stop_or("retrieve"), ["retrieve", END])
        g.add_edge("abstain", END)
        g.add_conditional_edges("generate", self._stop_or("validate"), ["validate", END])
        g.add_edge("validate", END)
        return g.compile()

    def _wrap(self, name: str, fn: Callable[[AgentState], dict]) -> Callable[[AgentState], dict]:
        def run(state: AgentState) -> dict:
            control = self.deps.control
            if control is not None and control.cancelled():  # LLM-05: the client went away
                return {
                    "outcome": "error",
                    "message_key": "error",
                    "error_code": "cancelled",
                    "reason": f"cancelled before {name}",
                    "steps": [{"node": name, "seconds": 0.0, "skipped": True}],
                }
            if self.deps.clock() > state["deadline"]:  # RET-09: wall-clock cap
                return {
                    "outcome": "error",
                    "message_key": "error",
                    "error_code": "time_limit",
                    "reason": f"time limit of {self.s.max_request_seconds}s reached before {name}",
                    "steps": [{"node": name, "seconds": 0.0, "skipped": True}],
                }
            if control is not None:
                control.stage(name)
            with tracer.start_as_current_span(f"agent.{name}") as span:
                started = self.deps.clock()
                update = fn(state)
                seconds = round(self.deps.clock() - started, 3)
                if update.get("outcome"):
                    span.set_attribute("fxa.outcome", update["outcome"])
            update["steps"] = [{"node": name, "seconds": seconds}]
            return update

        return run

    @staticmethod
    def _stop_or(next_node: str) -> Callable[[AgentState], str]:
        return lambda state: END if state.get("outcome") else next_node

    def _after_retrieve(self, state: AgentState) -> str:
        if state.get("outcome"):
            return END
        if not state.get("hits"):
            return "rewrite" if self._can_rewrite(state) else "abstain"
        if state.get("advice") or not self.s.grader_enabled:
            return "generate"
        return "grade"

    def _after_grade(self, state: AgentState) -> str:
        if state.get("outcome"):
            return END
        if state.get("relevant"):
            return "generate"
        return "rewrite" if self._can_rewrite(state) else "abstain"

    def _can_rewrite(self, state: AgentState) -> bool:
        return not state.get("advice") and state.get("rewrites", 0) < self.s.max_rewrites

    # --- Nodes ---------------------------------------------------------------------

    def _guard(self, state: AgentState) -> dict:
        result = check_question(state["question"], max_chars=self.s.max_question_chars)
        if result.kind == "ok":
            return {}
        if result.kind == "advice":  # SAF-01/02: decline, then look for documented risk warnings
            return {"advice": True, "query": ADVICE_QUERY, "reason": result.reason}
        outcome, key = {
            "empty": ("clarify", "clarify"),
            "vague": ("clarify", "clarify"),  # RET-04
            "too_long": ("refused", "too_long"),
            "injection": ("refused", "injection"),  # SAF-04
            "reveal": ("refused", "reveal"),  # SAF-05
        }[result.kind]
        return {"outcome": outcome, "message_key": key, "reason": result.reason}

    def _retrieve(self, state: AgentState) -> dict:
        query = state.get("query") or state["question"]
        vector = self.deps.embedder.embed_query(query)
        raw = self.deps.store.search(vector, limit=self.s.top_k * self.s.candidate_multiplier)
        candidates = select_hits(
            raw,
            top_k=self.s.top_k,
            max_per_source=self.s.max_per_source,
            duplicate_similarity=self.s.near_duplicate_similarity,
        )
        update: dict[str, Any] = {}
        if "retrieved" not in state:
            update["retrieved"] = [
                {"source_id": h.source_id, "score": round(h.score, 4), "page": h.page}
                for h in candidates
            ]
        best = raw[0].score if raw else 0.0
        if best < self.s.out_of_scope_threshold and not state.get("advice"):
            return update | {  # RET-05: nothing in the corpus is even close
                "outcome": "out_of_scope",
                "message_key": "out_of_scope",
                "reason": f"best retrieval score {best:.3f} < {self.s.out_of_scope_threshold}",
            }
        hits = [h for h in candidates if h.score >= self.s.score_threshold]  # RET-01
        return update | {"hits": hits}

    def _grade(self, state: AgentState) -> dict:
        hits = state["hits"]
        excerpts = _label(hits)
        raw = self.deps.llm.complete(
            prompts.grader_messages(state["question"], excerpts), max_tokens=60, json_mode=True
        )
        try:  # RET-07: anything malformed means "not relevant", never a crash
            labels = json.loads(raw)["relevant"]
            if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
                raise TypeError("'relevant' must be a list of labels")
        except (ValueError, KeyError, TypeError) as exc:
            log.error("grader returned malformed output (%s): %.200r", exc, raw)
            return {"relevant": [], "grader_errors": state.get("grader_errors", 0) + 1}
        wanted = {label.strip().upper() for label in labels}
        return {"relevant": [h for e, h in zip(excerpts, hits, strict=True) if e.label in wanted]}

    def _rewrite(self, state: AgentState) -> dict:
        raw = self.deps.llm.complete(prompts.rewrite_messages(state["question"]), max_tokens=40)
        line = (raw.strip().splitlines() or [""])[0].strip().strip("\"'")[:200]
        return {
            "query": prompts.neutralise(line) or state["question"],
            "rewrites": state.get("rewrites", 0) + 1,
            "hits": [],
            "relevant": [],
        }

    def _abstain(self, state: AgentState) -> dict:
        return {
            "outcome": "abstained",
            "message_key": "abstained",
            "reason": "no excerpt was relevant enough",  # RET-01: no LLM guess
        }

    def fit_context(self, question: str, hits: list[Hit], *, advice: bool) -> tuple[list[Hit], int]:
        """RET-06: keep the best-ranked hits that fit the model's context window.

        Returns the hits to use and how many were dropped or cut short.
        """
        system = prompts.SYSTEM_PROMPT + (prompts.ADVICE_ADDENDUM if advice else "")
        overhead = prompts.estimate_tokens(system + question) + 100
        budget = self.s.llm_max_context_tokens - self.s.llm_max_output_tokens - overhead
        kept: list[Hit] = []
        used, cut = 0, False
        for hit in hits:
            cost = prompts.estimate_tokens(hit.text) + 25  # tag and label overhead
            if used + cost > budget:
                if not kept and budget > 50:  # even the best excerpt is too long: cut it
                    kept.append(replace(hit, text=hit.text[: budget * 3]))
                    cut = True
                break
            kept.append(hit)
            used += cost
        return kept, len(hits) - len(kept) + int(cut)

    def _generate(self, state: AgentState) -> dict:
        advice = bool(state.get("advice"))
        candidates = state.get("relevant") or state.get("hits") or []
        context, truncated = self.fit_context(state["question"], candidates, advice=advice)
        excerpts = _label(context)
        text = self.deps.llm.complete(
            prompts.answer_messages(state["question"], excerpts, advice=advice),
            max_tokens=self.s.llm_max_output_tokens,
        )
        if truncated:
            log.info("context truncated: %d excerpt(s) dropped or cut", truncated)
        if not text.strip() or prompts.says_insufficient(text):
            return {
                "outcome": "abstained",
                "message_key": "abstained",
                "reason": "model said the excerpts do not answer the question"
                if text.strip()
                else "model returned an empty answer",
                "context": context,
                "truncated": truncated,
            }
        return {"answer": text, "context": context, "truncated": truncated}

    def _validate(self, state: AgentState) -> dict:
        context = state["context"]
        excerpts = _label(context)
        check = validate(state["answer"], {e.label: e.text for e in excerpts})
        if not check.ok:  # RET-08, SAF-03
            return {
                "outcome": "abstained",
                "message_key": "abstained",
                "reason": f"answer rejected: {check.reason}",
                "rejected_answer": state["answer"],
            }
        by_label = {e.label: hit for e, hit in zip(excerpts, context, strict=True)}
        citations = [
            Citation(label, h.source_id, h.title, h.publisher, h.url, h.page)
            for label in check.cited
            for h in [by_label[label]]
        ]
        return {"outcome": "answered", "answer": check.answer, "citations": citations}

    def answer_from_hits(self, question: str, hits: list[Hit]) -> AgentResult:
        """Run generate and validate on given excerpts, skipping retrieval.

        Used by the evaluation scenarios that plant crafted excerpts (RET-02, RET-03).
        """
        started = self.deps.clock()
        state: dict = {
            "question": question,
            "deadline": started + self.s.max_request_seconds,
            "hits": hits,
            "steps": [],
        }
        for node in (self._generate, self._validate):
            try:
                state.update(node(state))
            except LLMError as exc:
                state.update(
                    outcome="error", message_key="error", error_code=exc.code, reason=str(exc)
                )
            if state.get("outcome"):
                break
        return self._result(state, started)

    # --- Public API ------------------------------------------------------------------

    def ask(self, question: str) -> AgentResult:
        started = self.deps.clock()
        initial: AgentState = {
            "question": question,
            "deadline": started + self.s.max_request_seconds,
            "rewrites": 0,
            "steps": [],
        }
        try:
            with tracer.start_as_current_span("agent.ask"):
                state = self.graph.invoke(initial, {"recursion_limit": self.s.max_graph_steps})
        except GraphRecursionError:  # RET-09: step cap
            state = {
                **initial,
                "outcome": "error",
                "message_key": "error",
                "error_code": "step_limit",
                "reason": f"step limit of {self.s.max_graph_steps} reached",
            }
        except LLMError as exc:
            log.error("LLM failure (%s): %s", exc.code, exc)
            state = {
                **initial,
                "outcome": "error",
                "message_key": "error",
                "error_code": exc.code,
                "reason": str(exc),
            }
        except StoreUnavailableError as exc:  # DEP-01: never answer without the documents
            log.error("vector store failure: %s", exc)
            state = {
                **initial,
                "outcome": "error",
                "message_key": "error",
                "error_code": "store_unavailable",
                "reason": str(exc),
            }
        return self._result(state, started)

    def _result(self, state: dict, started: float) -> AgentResult:
        outcome = state.get("outcome", "error")
        answer = state.get("answer", "") if outcome == "answered" else ""
        if state.get("advice"):
            # The decline sentence comes from code, not the model, so it is always present.
            general = f"\n\nWhat the documents say about the risks:\n{answer}" if answer else ""
            outcome, answer = "declined_advice", MESSAGES["advice"] + general
        elif outcome != "answered":
            answer = MESSAGES.get(state.get("message_key", outcome), MESSAGES["error"])
        try:
            corpus_version = self.deps.store.corpus_version()
        except Exception:  # informational only
            corpus_version = None
        return AgentResult(
            question=state["question"],
            outcome=outcome,
            answer=answer,
            citations=state.get("citations", []) if state.get("outcome") == "answered" else [],
            reason=state.get("reason", ""),
            retrieved=state.get("retrieved", []),
            steps=state.get("steps", []),
            truncated=state.get("truncated", 0),
            grader_errors=state.get("grader_errors", 0),
            seconds=round(self.deps.clock() - started, 3),
            corpus_version=corpus_version,
            rejected_answer=state.get("rejected_answer"),
            error_code=(state.get("error_code") or "agent_error") if outcome == "error" else None,
        )
