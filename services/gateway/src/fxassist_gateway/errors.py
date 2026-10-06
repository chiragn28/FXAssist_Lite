"""API errors: one JSON shape, stable codes, and the mapping from agent failures to HTTP.

Body: {"error": {"code": "...", "message": "...", "request_id": "..."}}
Messages are written for the caller and never include internals (API-01, OBS-02).
"""

from __future__ import annotations

import math


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retry_after: float | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = dict(headers or {})
        if retry_after is not None:
            self.headers["Retry-After"] = str(max(1, math.ceil(retry_after)))

    def body(self, request_id: str | None) -> dict:
        return {"error": {"code": self.code, "message": self.message, "request_id": request_id}}


# Agent error codes (AgentResult.error_code, from fxassist_agent.llm) -> (HTTP status, message)
AGENT_ERRORS: dict[str, tuple[int, str]] = {
    "llm_timeout": (504, "The language model did not respond in time. Please try again."),
    "time_limit": (504, "Answering took too long. Please try again."),
    "llm_unavailable": (503, "The language model is unavailable. Please try again shortly."),
    "llm_circuit_open": (503, "The language model is failing; requests are paused briefly."),
    "llm_model_missing": (503, "The configured language model is not available."),
    "store_unavailable": (503, "The document index is unavailable. Please try again shortly."),
    "llm_empty": (502, "The language model returned an empty answer."),
    "llm_bad_response": (502, "The language model's response broke off or was unusable."),
    "llm_context_overflow": (500, "The request did not fit the model's context window."),
    "step_limit": (500, "The agent stopped after too many steps."),
}


def from_agent_error(code: str | None, retry_after: float | None = None) -> ApiError:
    status, message = AGENT_ERRORS.get(code or "", (500, "Something went wrong answering that."))
    if status == 503 and retry_after is None:
        retry_after = 5
    return ApiError(status, code or "agent_error", message, retry_after=retry_after)
