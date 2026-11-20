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


class OpenAICompatibleClient:
    """Any OpenAI-compatible chat-completions API: Ollama (local, free), Gemini's OpenAI endpoint, Groq, OpenRouter, vLLM.

    Structured output: `json_schema` response_format where supported; on a 400 the client falls back to
    `json_object`, then to schema-in-prompt. The reply is always validated against the Pydantic schema, with
    one corrective retry; anything still invalid raises LLMError so the agent falls back to its rule-based report.
    """

    provider = "openai_compatible"

    def __init__(self, settings: Settings, transport: Any = None):
        import httpx

        if not settings.llm_base_url:
            raise LLMError("LLM_BASE_URL is not configured")
        self.base = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.max_tokens = settings.llm_max_tokens
        self.mode = settings.llm_json_mode
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key is not None:
            headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
        self.http = httpx.Client(timeout=settings.llm_timeout_seconds, headers=headers, transport=transport)

    @staticmethod
    def _extract_json(text: str) -> str:
        import re

        text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()  # reasoning models
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
        if fence:
            text = fence.group(1).strip()
        start, end = text.find("{"), text.rfind("}")
        return text[start:end + 1] if start != -1 and end > start else text

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        import httpx

        for attempt in range(3):
            try:
                r = self.http.post(f"{self.base}/chat/completions", json=body)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise LLMError(f"connection error: {exc}", retryable=True) from exc
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 2:
                    raise LLMError(f"provider error HTTP {r.status_code}", retryable=True)
                time.sleep(float(r.headers.get("retry-after", 2 ** (attempt + 1))))
                continue
            if r.status_code == 400:
                raise LLMError(f"bad request: {r.text[:300]}", stop_reason="bad_request")
            if r.status_code >= 400:
                raise LLMError(f"API error HTTP {r.status_code}: {r.text[:200]}")
            return r.json()
        raise LLMError("provider unavailable", retryable=True)

    def structured(self, system: str, user: str, schema: type[T], max_tokens: int | None = None) -> LLMResult:
        import json

        from pydantic import ValidationError

        js = schema.model_json_schema()
        sys_full = (system + "\n\nReturn ONLY one JSON object (no prose, no markdown) that validates against this JSON "
                    f"Schema:\n{json.dumps(js)}")
        messages: list[dict[str, str]] = [{"role": "system", "content": sys_full}, {"role": "user", "content": user}]
        modes = {"json_schema": ["json_schema", "json_object", "prompt"], "json_object": ["json_object", "prompt"],
                 "prompt": ["prompt"]}.get(self.mode, ["prompt"])
        t0 = time.monotonic()
        usage_in = usage_out = 0
        last_err = "no response"
        for _ in range(2):  # one corrective retry on invalid JSON / schema mismatch
            data = None
            for mode in list(modes):
                body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": max_tokens or self.max_tokens,
                                        "temperature": 0}
                if mode == "json_schema":
                    body["response_format"] = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": js}}
                elif mode == "json_object":
                    body["response_format"] = {"type": "json_object"}
                try:
                    data = self._post(body)
                    break
                except LLMError as exc:
                    if exc.stop_reason == "bad_request" and mode != modes[-1]:
                        modes.remove(mode)  # provider does not support this mode; remember for later calls
                        self.mode = modes[0]
                        continue
                    raise
            choice = (data or {}).get("choices", [{}])[0]
            u = (data or {}).get("usage") or {}
            usage_in += u.get("prompt_tokens") or 0
            usage_out += u.get("completion_tokens") or 0
            finish = choice.get("finish_reason")
            text = (choice.get("message") or {}).get("content") or ""
            if finish == "length":
                raise LLMError("output truncated at max_tokens", stop_reason="max_tokens")
            try:
                parsed = schema.model_validate_json(self._extract_json(text))
                return LLMResult(parsed=parsed, provider=self.provider, model=(data or {}).get("model", self.model),
                                 input_tokens=usage_in or None, output_tokens=usage_out or None,
                                 latency_ms=int((time.monotonic() - t0) * 1000), stop_reason=finish)
            except (ValidationError, ValueError) as exc:
                last_err = str(exc)[:600]
                messages = messages + [{"role": "assistant", "content": text[:4000]},
                                       {"role": "user", "content": "That reply was not valid JSON for the schema. Errors: "
                                        f"{last_err}\nReply again with ONLY the corrected JSON object."}]
        raise LLMError(f"structured output invalid after retry: {last_err}", stop_reason="invalid_output")


def build_llm(settings: Settings) -> LLMClient | None:
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key is not None:
        return AnthropicClient(settings)
    if settings.llm_provider == "openai_compatible" and settings.llm_configured:
        return OpenAICompatibleClient(settings)
    return None
