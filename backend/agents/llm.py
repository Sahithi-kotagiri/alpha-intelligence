"""Provider-agnostic LLM interface.

Business logic depends only on `LLMClient.structured(system, user, schema) -> LLMResult`.
Adding a provider means implementing that one method; agents, orchestration and
tests are unchanged. `FakeLLM` returns scripted outputs for deterministic tests.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from backend.app.config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False, stop_reason: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.stop_reason = stop_reason


class LLMResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    parsed: Any
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int
    stop_reason: str | None = None


class LLMClient(Protocol):
    provider: str
    model: str

    def structured(self, system: str, user: str, schema: type[T], max_tokens: int | None = None) -> LLMResult: ...


class AnthropicClient:
    """Claude via the official Anthropic SDK with structured outputs (Pydantic schema)."""

    provider = "anthropic"

    def __init__(self, settings: Settings, http_client: Any = None):
        import anthropic

        if settings.anthropic_api_key is None:
            raise LLMError("ANTHROPIC_API_KEY is not configured")
        self._anthropic = anthropic
        kwargs: dict[str, Any] = {
            "api_key": settings.anthropic_api_key.get_secret_value(),
            "base_url": settings.claude_api_base_url,  # pinned: never inherit ANTHROPIC_BASE_URL meant for other tools
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 2,
        }
        if http_client is not None:
            kwargs["http_client"] = http_client
        self.client = anthropic.Anthropic(**kwargs)
        self.model = settings.llm_model
        self.effort = settings.llm_effort
        self.max_tokens = settings.llm_max_tokens
        self.server_fallback = settings.llm_server_fallback

    def structured(self, system: str, user: str, schema: type[T], max_tokens: int | None = None) -> LLMResult:
        a = self._anthropic
        t0 = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
            "output_config": {"effort": self.effort},
            "thinking": {"type": "adaptive"},
        }
        try:
            if self.server_fallback:
                resp = self.client.beta.messages.parse(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.parse(**kwargs)
        except a.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}", retryable=True) from exc
        except a.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500) from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}", retryable=True) from exc
        latency = int((time.monotonic() - t0) * 1000)
        if resp.stop_reason == "refusal":
            raise LLMError("model declined the request (refusal)", stop_reason="refusal")
        if resp.stop_reason == "max_tokens":
            raise LLMError("output truncated at max_tokens", retryable=False, stop_reason="max_tokens")
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise LLMError("structured output missing or invalid", stop_reason=resp.stop_reason)
        return LLMResult(parsed=parsed, provider=self.provider, model=getattr(resp, "model", self.model),
                         input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                         latency_ms=latency, stop_reason=resp.stop_reason)


class FakeLLM:
    """Scripted LLM for tests: responder(system, user, schema) -> dict or BaseModel."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, responder: Callable[[str, str, type[BaseModel]], Any]):
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def structured(self, system: str, user: str, schema: type[T], max_tokens: int | None = None) -> LLMResult:
        self.calls.append({"system": system, "user": user, "schema": schema.__name__})
        out = self.responder(system, user, schema)
        if isinstance(out, Exception):
            raise out
        parsed = out if isinstance(out, BaseModel) else schema.model_validate(out)
        return LLMResult(parsed=parsed, provider=self.provider, model=self.model, input_tokens=len(user) // 4,
                         output_tokens=100, latency_ms=1, stop_reason="end_turn")


def build_llm(settings: Settings) -> LLMClient | None:
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key is not None:
        return AnthropicClient(settings)
    return None
