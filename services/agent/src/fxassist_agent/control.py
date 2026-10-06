"""Per-request control shared between the gateway (async) and the agent (a worker thread).

The agent runs synchronously in a thread. The gateway cannot interrupt a thread, so it asks
politely instead: it sets `cancel_event` when the client goes away (LLM-05), and the agent
checks it before every graph node and the LLM adapter checks it between streamed chunks.
Timings recorded here feed the request log and the metrics that separate retrieval, queueing,
time to first token and total time (OBS-05).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class LLMCallStats:
    kind: str  # "text" or "json" (grader)
    started: float
    attempts: int = 0
    ttft_s: float | None = None  # time to first content token, from the start of the call
    total_s: float | None = None
    output_chars: int = 0
    completion_tokens: int | None = None  # as reported by the server, when it does
    prompt_tokens: int | None = None
    malformed_chunks: int = 0
    error: str | None = None


@dataclass
class RunControl:
    cancel_event: threading.Event = field(default_factory=threading.Event)
    on_stage: Callable[[str], None] | None = None  # called with each graph node name
    llm_calls: list[LLMCallStats] = field(default_factory=list)

    def cancel(self) -> None:
        self.cancel_event.set()

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def stage(self, name: str) -> None:
        if self.on_stage is not None:
            self.on_stage(name)
