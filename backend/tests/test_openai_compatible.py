"""OpenAI-compatible adapter (Ollama / Gemini / Groq / OpenRouter) tested against a mock HTTP server."""

import json

import httpx
import pytest
from pydantic import BaseModel

from backend.agents.llm import LLMError, OpenAICompatibleClient, build_llm
from backend.app.config import Settings


class Out(BaseModel):
    answer: str
    score: int


def settings(**kw):
    base = {"_env_file": None, "llm_provider": "openai_compatible", "llm_base_url": "http://localhost:11434/v1",
            "llm_model": "qwen3:8b"}
    return Settings(**(base | kw))


def reply(content, status=200, finish="stop"):
    return httpx.Response(status, json={"model": "qwen3:8b", "choices": [{"message": {"content": content}, "finish_reason": finish}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


def client(handler, **kw):
    return OpenAICompatibleClient(settings(**kw), transport=httpx.MockTransport(handler))


def test_build_llm_selection():
    assert build_llm(settings()).provider == "openai_compatible"
    assert build_llm(settings(llm_model="claude-opus-5-5")) is None  # a Claude model name cannot be served by Ollama
    assert build_llm(Settings(_env_file=None, llm_provider="none")) is None


def test_json_schema_request_and_parse():
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        seen["auth"] = req.headers.get("authorization")
        return reply('{"answer": "ok", "score": 3}')

    r = client(h, llm_api_key="free-key").structured("sys", "user", Out)
    assert r.parsed.score == 3 and r.input_tokens == 10
    assert seen["response_format"]["type"] == "json_schema" and seen["temperature"] == 0
    assert seen["auth"] == "Bearer free-key" and seen["model"] == "qwen3:8b"


def test_falls_back_when_json_schema_unsupported():
    calls = []

    def h(req):
        body = json.loads(req.content)
        calls.append(body.get("response_format", {}).get("type"))
        if calls[-1] == "json_schema":
            return httpx.Response(400, json={"error": "response_format json_schema not supported"})
        return reply('{"answer": "ok", "score": 1}')

    c = client(h)
    assert c.structured("s", "u", Out).parsed.answer == "ok"
    assert calls == ["json_schema", "json_object"] and c.mode == "json_object"


def test_strips_think_tags_and_fences_then_retries_invalid():
    replies = iter(['<think>hmm</think>not json', '```json\n{"answer": "fixed", "score": 2}\n```'])

    def h(req):
        return reply(next(replies))

    assert client(h).structured("s", "u", Out).parsed.answer == "fixed"


def test_invalid_twice_raises_so_agent_falls_back():
    with pytest.raises(LLMError) as e:
        client(lambda req: reply('{"answer": 1}')).structured("s", "u", Out)
    assert e.value.stop_reason == "invalid_output"


def test_rate_limit_retry_and_truncation(monkeypatch):
    monkeypatch.setattr("backend.agents.llm.time.sleep", lambda s: None)
    seq = iter([httpx.Response(429, headers={"retry-after": "0"}), reply('{"answer": "a", "score": 0}')])
    assert client(lambda req: next(seq)).structured("s", "u", Out).parsed.answer == "a"
    with pytest.raises(LLMError) as e:
        client(lambda req: reply("{", finish="length")).structured("s", "u", Out)
    assert e.value.stop_reason == "max_tokens"


def test_full_graph_through_openai_compatible_adapter(providers):
    """Every agent, the committee and the final decision run through the adapter over (mock) HTTP."""
    from backend.agents.llm import FakeLLM  # noqa: F401  (responder shared with the agent tests)
    from backend.tests.test_agents import make_responder, run_graph

    responder = make_responder()
    names = []

    def h(req):
        body = json.loads(req.content)
        name = body["response_format"]["json_schema"]["name"]
        names.append(name)
        schema_cls = type(name, (), {})
        out = responder(body["messages"][0]["content"], body["messages"][1]["content"], schema_cls)
        return reply(json.dumps(out))

    llm = client(h)
    st, _ = run_graph(providers, llm)
    assert {r.mode for r in st["reports"].values()} == {"llm"}
    assert {"FundamentalNarrative", "TechnicalNarrative", "NewsNarrative", "RiskNarrative", "DevilsAdvocateNarrative",
            "CommitteeNarrative", "FinalNarrative"} <= set(names)
    assert st["final"]["mode"] == "llm" and st["final"]["recommendation"] in st["committee"]["allowed"]
