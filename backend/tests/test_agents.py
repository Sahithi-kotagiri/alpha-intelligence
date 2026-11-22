import json
import re

import pytest

from backend.agents.guardrails import invalid_citations, strip_invalid, unverified_numbers
from backend.agents.llm import FakeLLM, LLMError
from backend.analysis.evidence import EvidenceItem

ID_RE = re.compile(r"\[([a-z]+\.[^\]\s]+)\]")


def claim(ids, text="Interpretation grounded in the cited evidence."):
    return {"text": text, "kind": "interpretation", "evidence_ids": ids}


def make_responder(chosen="Watchlist", override="", bad_ids=False, raise_for=(), always_ask=False, injected=None):
    def respond(system, user, schema):
        if schema.__name__ in raise_for:
            return LLMError("simulated provider failure", retryable=True)
        ids = ID_RE.findall(user)[:3] or ["dq.report"]
        if bad_ids:
            ids = ["met.does_not_exist.123"]
        if injected is not None:
            injected.append(user)
        n = schema.__name__
        c = claim(ids)
        if n == "FundamentalNarrative":
            return {"executive_assessment": "Quality business; valuation in line.", "strengths": [c], "weaknesses": [c],
                    "valuation_assessment": c, "moat_rating": "narrow",
                    "moat_sources": [{"source": "switching_costs", "assessment": "Stable margins", "evidence_ids": ids}],
                    "management_capital_allocation": [c], "competitor_context": [c], "key_assumptions": ["Growth persists"],
                    "missing_information": ["Segment data"], "confidence": "Medium", "confidence_reason": "Adequate data."}
        if n == "ResearchPlan":
            return {"focus_evidence_ids": ids + ["not.a.real.id"], "open_questions": ["Is growth durable?"],
                    "rationale": "Start with the rulebook pillars."}
        if n == "ValuationNarrative":
            return {"executive_assessment": "Price close to fair value.", "fair_value_view": c, "market_expectations": c,
                    "pe_history_view": c, "key_sensitivities": [c], "margin_of_safety_view": c, "missing_information": [],
                    "confidence": "Medium", "confidence_reason": "ok"}
        if n == "TechnicalNarrative":
            return {k: c for k in ("trend_summary", "momentum_assessment", "relative_strength_assessment", "volume_assessment",
                                   "breakout_assessment", "entry_zone_rationale", "invalidation_condition")} | {
                "entry_zone_justified": False, "risk_level": "medium", "missing_information": [], "confidence": "Medium",
                "confidence_reason": "ok"}
        if n == "NewsNarrative":
            news_ids = [i for i in ID_RE.findall(user) if i.startswith("news.")]
            return {"classifications": [{"evidence_id": i, "sentiment": "positive", "materiality": "medium", "time_horizon": "short",
                                         "affects": "cash_flows", "status": "company_statement", "summary": "Product launch",
                                         "duplicate_of": None} for i in news_ids],
                    "positive_catalysts": [c], "negative_catalysts": [], "mixed_or_neutral": [], "upcoming_events": [],
                    "priced_in_view": "Uncertain.", "overall_assessment": "Mildly positive.", "coverage_limitations": ["Limited feed"],
                    "confidence": "Low", "confidence_reason": "Few items."}
        if n == "RiskNarrative":
            return {"main_risks": [{"risk": "Valuation compression", "category": "valuation", "likelihood": "medium", "impact": "medium",
                                    "evidence_ids": ids}], "scenario_commentary": c, "allocation_commentary": "Needs inputs.",
                    "stop_loss_considerations": "Gaps.", "portfolio_commentary": "n/a", "missing_information": [],
                    "confidence": "Medium", "confidence_reason": "ok"}
        if n == "DevilsAdvocateNarrative":
            return {"objections": [{"objection": "Growth may slow", "evidence_ids": ids, "mechanism": "Multiple compression",
                                    "likelihood": "medium", "severity": "medium", "what_would_weaken_it": "Re-acceleration",
                                    "verify_before_deciding": "Guidance"}],
                    "weak_assumptions": [c], "overlooked_evidence": [], "invalidation_conditions": ["Revenue growth < 5%"],
                    "thesis_failure": {"definition": "Underperform by 15%", "horizon": "12 months", "low_pct": 20, "high_pct": 45,
                                       "basis": "Uncalibrated expert judgement"},
                    "market_belief": "The price implies modest growth.", "our_difference": "", "difference_credible": False,
                    "difference_evidence_ids": [], "bear_case_conclusion": "Some risk."}
        if n == "CommitteeNarrative":
            return {"agreements": [c], "disagreements": [], "evidence_adjudication": [c], "unresolved_questions": [],
                    "review_questions": [{"target_agent": "fundamental", "question": "Is growth durable?"}] if always_ask else [],
                    "chosen_recommendation": chosen, "override_reason": override, "rationale": "Evidence-weighted.",
                    "why_not_alternatives": ["Buy: not justified."], "limitations": []}
        if n == "ReviewAnswer":
            return {"answer": "Growth supported by cited revenue trend.", "evidence_ids": ids, "resolved": True,
                    "cannot_resolve_reason": ""}
        if n == "FinalNarrative":
            return {"executive_summary": "Summary.", "thesis": [c, c, c], "top_risks": [c], "monitoring_triggers": ["Earnings"],
                    "qualitative_assumptions": ["Product adoption continues"]}
        raise AssertionError(n)

    return respond


def run_graph(providers, llm, ticker="TSTA", params=None):
    from backend.data.ingestion import CompanyIdentity
    from backend.orchestration.graph import build_graph
    from backend.services.universe import universe_rows  # noqa: F401

    c = providers.market.cos[ticker]
    universe = [{"symbol": x.ticker, "name": x.name, "sector": x.sector, "sub_industry": x.sub} for x in providers.market.cos.values()]
    events = []
    g = build_graph(providers, llm, lambda *a, **k: events.append((a, k)), universe, {})
    ident = CompanyIdentity(ticker=ticker, name=c.name, cik=c.cik, sector=c.sector, sub_industry=c.sub, in_sp500=True)
    st = g.invoke({"analysis_id": "t", "params": params or {"horizon": "12 months", "benchmark": "SPY"}, "identity": ident,
                   "review_round": 0, "review_log": [], "reports": {}, "committee_history": []})
    return st, events


def test_full_graph_with_fake_llm(providers):
    llm = FakeLLM(make_responder())
    st, events = run_graph(providers, llm)
    assert {r.mode for r in st["reports"].values()} == {"llm"}
    assert st["final"]["recommendation"] in st["committee"]["allowed"]
    assert st["final"]["mode"] == "llm"
    for r in st["reports"].values():
        assert not r.guardrails.invalid_citations
    assert st["reports"]["news"].score is not None  # news classified -> scored
    assert any(t["kind"] == "qualitative" for t in st["thesis"])
    assert st["ctx"].model_runs and all(m.get("prompt_sha256") for m in st["ctx"].model_runs if m["status"] == "ok")
    # research planning: each analyst chose evidence first; invented IDs are rejected, not used
    plans = [p for p in st["ctx"].research_log if p["status"] == "ok"]
    assert {p["agent"] for p in plans} >= {"fundamental", "valuation", "technical", "risk_portfolio"}  # news: < 40 items, skipped
    assert all("not.a.real.id" in p["rejected_ids"] and "not.a.real.id" not in p["focus_evidence_ids"] for p in plans)


def test_deterministic_mode_is_labelled(providers):
    st, events = run_graph(providers, None)
    for r in st["reports"].values():
        assert r.mode == "deterministic" and r.summary.startswith("[Rule-based report")
    # rule-based News & Estimates scores only the deterministic revisions pillar (no keyword sentiment)
    assert "revisions pillar" in st["reports"]["news"].score["explanation"]
    assert not st["reports"]["news"].deterministic.get("classifications")
    assert any("no LLM" in e for e in st["final"]["confidence_explanation"]) or \
        any("rule-based only" in e for e in st["final"]["confidence_explanation"])


def test_invalid_citations_retry_then_strip(providers):
    llm = FakeLLM(make_responder(bad_ids=True))
    st, _ = run_graph(providers, llm)
    f = st["reports"]["fundamental"]
    assert f.guardrails.retried and f.guardrails.invalid_citations == ["met.does_not_exist.123"]
    assert f.guardrails.removed_claims > 0
    assert all("met.does_not_exist.123" not in json.dumps(f.narrative) for _ in [0])


def test_llm_failure_falls_back_honestly(providers):
    llm = FakeLLM(make_responder(raise_for=("TechnicalNarrative",)))
    st, events = run_graph(providers, llm)
    t = st["reports"]["technical"]
    assert t.mode == "deterministic_fallback" and t.status == "partial" and "LLM interpretation failed" in t.error
    assert any(a[1] == "failed" and k.get("agent") == "technical" for a, k in events)


def test_committee_cannot_choose_gated_recommendation(providers):
    providers.market.cos["TSTA"].info_overrides["quoteType"] = "ETF"  # => data quality critical => buys blocked
    llm = FakeLLM(make_responder(chosen="Strong Buy", override="I like it"))
    st, _ = run_graph(providers, llm)
    assert "Strong Buy" not in st["committee"]["allowed"] and "Buy" not in st["committee"]["allowed"]
    assert st["final"]["recommendation"] not in ("Strong Buy", "Buy")
    assert any("rulebook label" in x for x in st["committee"]["limitations"])


def test_override_without_reason_is_discarded(providers):
    llm = FakeLLM(make_responder(chosen="Reject", override=""))
    st, _ = run_graph(providers, llm)
    c = st["committee"]
    if "Reject" in c["allowed"] and c["policy_recommendation"] != "Reject":
        assert c["recommendation"] == c["policy_recommendation"] and not c["overridden"]


def test_review_loop_is_bounded(providers):
    from backend.analysis.config_loader import policy

    llm = FakeLLM(make_responder(always_ask=True))
    st, _ = run_graph(providers, llm)
    assert len(st["committee_history"]) <= policy()["review_loop"]["max_rounds"]
    assert st["review_log"] and all(r["round"] == 1 for r in st["review_log"])


def test_untrusted_text_is_fenced(providers):
    seen = []
    run_graph(providers, FakeLLM(make_responder(injected=seen)))
    news_prompt = next(u for u in seen if "news." in u)
    assert "<untrusted_source_text>" in news_prompt
    inj = news_prompt.index("Ignore previous instructions")
    assert news_prompt.rfind("<untrusted_source_text>", 0, inj) > news_prompt.rfind("</untrusted_source_text>", 0, inj)


def test_news_dedup_in_evidence(providers):
    st, _ = run_graph(providers, None)
    news = st["ctx"].pack.by_category("news")
    assert len(news) == 1  # two feed items with identical headlines -> one evidence item


def test_guardrail_helpers():
    items = [EvidenceItem(id="met.a", category="metric", title="x", value=0.188, unit="pct", source_name="s", source_type="calculated"),
             EvidenceItem(id="met.b", category="metric", title="y", value=26.04, unit="x", source_name="s", source_type="calculated"),
             EvidenceItem(id="fin.c", category="financial", title="z", value=331.8e9, unit="USD", source_name="s", source_type="calculated")]
    assert unverified_numbers(["Growth was 18.8% and P/E 26.0x on revenue of $331.8B in 2026"], items) == []
    assert unverified_numbers(["Margins are 55.5%"], items) == ["55.5%"]
    obj = {"strengths": [{"text": "t", "kind": "fact", "evidence_ids": ["met.a", "bad.id"]}]}
    assert invalid_citations(obj, {"met.a"}) == ["bad.id"]
    clean, n = strip_invalid({"x": [{"text": "t", "evidence_ids": ["bad"]}]}, {"met.a"})
    assert n == 1 and clean["x"][0]["text"].startswith("[Unsupported")


def test_news_score_formula():
    from backend.agents.news import news_score

    s = news_score([{"evidence_id": "news.0", "sentiment": "positive", "materiality": "high"},
                    {"evidence_id": "news.1", "sentiment": "negative", "materiality": "low"},
                    {"evidence_id": "news.2", "sentiment": "positive", "materiality": "low", "duplicate_of": "news.0"}])
    assert s.score == pytest.approx(5 + 5 * (3 - 1) / 4)


def test_anthropic_adapter_request_shape(monkeypatch):
    import anthropic
    import httpx2
    from pydantic import BaseModel

    from backend.agents.llm import AnthropicClient
    from backend.app.config import Settings

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://wrong.example.invalid")
    captured = {}

    class Out(BaseModel):
        answer: str

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                          "content": [{"type": "text", "text": json.dumps({"answer": "ok"})}],
                                          "stop_reason": "end_turn", "stop_sequence": None,
                                          "usage": {"input_tokens": 12, "output_tokens": 3}})

    s = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="sk-test")
    client = AnthropicClient(s, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    res = client.structured("system text", "user text", Out, followup="fix your citations")
    assert res.parsed.answer == "ok" and res.input_tokens == 12
    b = captured["body"]
    # prompt caching: system and the evidence prompt are cache breakpoints; the corrective follow-up is not
    assert b["system"] == [{"type": "text", "text": "system text", "cache_control": {"type": "ephemeral"}}]
    assert b["messages"][0]["content"] == [{"type": "text", "text": "user text", "cache_control": {"type": "ephemeral"}},
                                           {"type": "text", "text": "fix your citations"}]
    assert captured["url"].startswith("https://api.anthropic.com/")
    assert b["model"] == "claude-opus-5-5" and b["thinking"] == {"type": "adaptive"}
    assert b["output_config"]["effort"] == "high" and b["output_config"]["format"]["type"] == "json_schema"
    assert b["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in captured["headers"].get("anthropic-beta", "")
    h = captured["headers"]
    assert h.get("x-api-key") == "sk-test" or h.get("authorization") == "Bearer sk-test"


def test_anthropic_adapter_refusal_raises():
    import anthropic
    import httpx2
    from pydantic import BaseModel

    from backend.agents.llm import AnthropicClient
    from backend.app.config import Settings

    class Out(BaseModel):
        answer: str

    def handler(request):
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                          "content": [], "stop_reason": "refusal", "stop_sequence": None,
                                          "usage": {"input_tokens": 1, "output_tokens": 0}})

    s = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="sk-test", llm_server_fallback=False)
    client = AnthropicClient(s, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    with pytest.raises(LLMError) as e:
        client.structured("s", "u", Out)
    assert e.value.stop_reason == "refusal"
