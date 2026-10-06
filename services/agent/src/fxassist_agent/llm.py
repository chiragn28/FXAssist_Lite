"""A minimal client for any OpenAI-compatible chat endpoint (ADR-003).

Phase 1 needs only non-streaming calls with a timeout. Phase 2 replaces this with the gateway's
adapter (streaming normalisation, retries with backoff, circuit breaker); the `ChatLLM`
protocol stays the same, so the agent does not change.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlparse

import httpx


class LLMError(RuntimeError):
    """The model server could not produce an answer."""


class LLMUnavailableError(LLMError):
    pass


class ModelNotFoundError(LLMError):
    pass


class ChatLLM(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> str: ...


def _is_local_ollama(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.hostname in {"localhost", "127.0.0.1", "ollama"} and parsed.port == 11434


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, *, api_key: str, timeout_s: float):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self._client.close()

    def _fix_hint(self) -> str:
        if _is_local_ollama(self.base_url):
            return "Start Ollama with `make up`, then download the model with `make pull-model`."
        return f"Check that the server at {self.base_url} is running and serves {self.model}."

    def check_model(self) -> None:
        """ENV-03: fail early with the exact fix instead of a stack trace mid-answer."""
        try:
            response = self._client.get("/models")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Cannot reach the LLM server at {self.base_url} ({type(exc).__name__}). "
                + self._fix_hint()
            ) from exc
        ids = {m.get("id") for m in response.json().get("data", [])}
        if self.model not in ids:
            raise ModelNotFoundError(
                f"Model '{self.model}' is not available at {self.base_url} "
                f"(available: {sorted(i for i in ids if i) or 'none'}). " + self._fix_hint()
            )

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> str:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": 0,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(f"LLM request timed out after {self._client.timeout.read}s") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Cannot reach the LLM server at {self.base_url}. " + self._fix_hint()
            ) from exc
        if response.status_code != 200:
            raise LLMError(f"LLM server returned HTTP {response.status_code}")
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("LLM server returned a malformed response") from exc
