"""Per-request control of the agent: cancellation (LLM-05), progress, error codes (DEP-01)."""

from __future__ import annotations

import json

from test_agent import LEVERAGE, seed

from agent_helpers import FakeLLM
from fxassist_agent.control import RunControl
from fxassist_agent.graph import Agent, AgentDeps
from fxassist_agent.llm import LLMTimeoutError
from fxassist_agent.store import StoreUnavailableError

GRADE_ALL = json.dumps({"relevant": ["S1"]})


def make(settings, store, embedder, llm, control=None) -> Agent:
    return Agent(
        AgentDeps(settings=settings, store=store, embedder=embedder, llm=llm, control=control)
    )


def test_progress_stages_are_reported_in_order(settings, store, embedder) -> None:
    seed(store, ("esma", LEVERAGE))
    seen: list[str] = []
    control = RunControl(on_stage=seen.append)
    llm = FakeLLM(grade=GRADE_ALL, answer="Major pairs: 30:1 [S1].")
    result = make(settings, store, embedder, llm, control).ask("What leverage limits apply?")
    assert result.outcome == "answered" and result.error_code is None
    assert seen == ["guard", "retrieve", "grade", "generate", "validate"]


def test_llm05_cancelled_run_stops_before_the_next_node(settings, store, embedder) -> None:
    seed(store, ("esma", LEVERAGE))
    control = RunControl()

    def grade_then_cancel(messages):
        control.cancel()  # the client disconnects while the grader runs
        return GRADE_ALL

    llm = FakeLLM(grade=grade_then_cancel, answer="30:1 [S1].")
    result = make(settings, store, embedder, llm, control).ask("What leverage limits apply?")
    assert result.outcome == "error" and result.error_code == "cancelled"
    assert llm.count("answer") == 0  # generation never started


def test_llm_error_code_reaches_the_result(settings, store, embedder) -> None:
    seed(store, ("esma", LEVERAGE))

    def timeout(messages):
        raise LLMTimeoutError("no first token")

    result = make(settings, store, embedder, FakeLLM(grade=timeout)).ask(
        "What leverage limits apply?"
    )
    assert result.outcome == "error" and result.error_code == "llm_timeout"


def test_dep01_vector_store_failure_is_an_error_not_an_answer(settings, store, embedder) -> None:
    seed(store, ("esma", LEVERAGE))

    def broken_search(vector, limit):
        raise StoreUnavailableError("connection refused")

    store.search = broken_search
    llm = FakeLLM(grade=GRADE_ALL, answer="30:1 [S1].")
    result = make(settings, store, embedder, llm).ask("What leverage limits apply?")
    assert result.outcome == "error" and result.error_code == "store_unavailable"
    assert llm.calls == []  # no model call, so nothing can be made up
