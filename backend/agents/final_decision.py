"""Final Decision Agent: converts committee output into one auditable report.

The recommendation, confidence, entry price and allocation come from the committee and
deterministic rules. The LLM (if configured) only writes the executive summary, thesis
bullets and risk wording, and cannot change the recommendation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.agents.base import AgentContext, load_prompt
from backend.agents.guardrails import claim_texts, strip_invalid, unverified_numbers
from backend.agents.llm import LLMClient, LLMError
from backend.analysis import decision as D
from backend.analysis.config_loader import versions
from backend.analysis.evidence import render_for_prompt
from backend.analysis.scoring import risk_rating
from backend.schemas.agents import Claim, FinalNarrative
from backend.tools.types import fmt_value


def _sources(ctx: AgentContext) -> list[dict[str, Any]]:
    seen, out = set(), []
    for i in ctx.pack.items.values():
        key = (i.source_name, i.source_url if i.category in ("news", "filing") else None)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": i.source_name, "url": i.source_url if i.category in ("news", "filing") else None,
                    "type": i.source_type, "category": i.category})
    return out[:150]


class FinalDecisionAgent:
    name = "final_decision"

    def build(self, ctx: AgentContext, committee: dict[str, Any], llm: LLMClient | None, emit) -> dict[str, Any]:
        emit("final_decision", "started", "Preparing final decision report", agent="final_decision")
        rec = committee["recommendation"]
        conf = committee["confidence"]
        snap_price = ctx.data_through.get("price")
        fund = ctx.reports.get("fundamental")
        tech = ctx.reports.get("technical")
        risk = ctx.reports.get("risk_portfolio")
        da = ctx.reports.get("devils_advocate")
        by = {a.metric_key: a for a in ctx.assessments}
        peer_stats = ctx.peer.get("stats") or {}
        eps_ttm = ctx.data_through.get("eps_ttm")
        fwd = ctx.pack.get("est.eps_next_fy")
        entry = D.entry_price(rec, snap_price, ctx.data_through.get("prices"),
                              (tech.deterministic.get("entry_zone") if tech else {}) or {}, ctx.assessments, eps_ttm,
                              (peer_stats.get("trailing_pe") or {}).get("median"), fwd.value if fwd else None,
                              (peer_stats.get("forward_pe") or {}).get("median"))
        alloc = D.allocation(rec, conf, ctx.portfolio, (ctx.risk or {}).get("vol_1y"))
        # thesis bullets and risks (rule-based baseline)
        thesis: list[Claim] = []
        if fund:
            thesis += fund.strengths[:3]
            thesis += [c for c in fund.claims if "valuation" in c.text.lower() or "P/E" in c.text][:1]
        if tech and tech.claims:
            thesis.append(tech.claims[0])
        thesis = thesis[:5]
        risks: list[dict[str, Any]] = []
        for o in ((da.deterministic.get("llm_objections") or da.deterministic.get("objections")) if da else []) or []:
            risks.append({"text": o["objection"], "likelihood": o["likelihood"], "impact": o["severity"],
                          "evidence_ids": o["evidence_ids"]})
        risks = sorted(risks, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["impact"]])[:6]
        triggers = []
        cal = ctx.pack.get("cal.next_earnings")
        if cal:
            triggers.append(f"Next earnings release (vendor calendar: {cal.display}; confirm with company).")
        triggers += ["Any new 10-Q/10-K filing (re-test thesis assumptions).",
                     "8-K items with high materiality (impairment, non-reliance, auditor change, cybersecurity, delisting).",
                     "Price move of more than 15% from the reference price or a close below the technical invalidation level."]
        inval = (da.deterministic.get("llm_invalidation_conditions") or da.deterministic.get("invalidation_conditions")) if da else []
        q, v = ctx.scores["fundamental_quality"].score, ctx.scores["valuation"].score
        rscore = ctx.scores["risk"].score if ctx.scores.get("risk") else None
        summary = (f"{ctx.company_name} ({ctx.ticker}) — {rec} at {conf:.0%} confidence (confidence reflects evidence sufficiency, "
                   f"not probability of profit). Business quality {q if q is not None else 'n/a'}/10; valuation "
                   f"{v if v is not None else 'n/a'}/10 versus {str(ctx.peer.get('level')).replace('_', '-')} peers ({ctx.peer.get('name')}); technical "
                   f"{committee['scores'].get('technical')}/10; risk rating {risk_rating(rscore)}. "
                   f"Decision basis: {committee['policy_trace'][-1] if committee['policy_trace'] else ''} "
                   + (f"Strongest objection: {risks[0]['text']} " if risks else "")
                   + f"Data quality: {ctx.dq.status}; prices through {ctx.data_through.get('prices')}, financials through "
                   f"{ctx.data_through.get('financials')}.")
        report: dict[str, Any] = {
            "recommendation": rec, "confidence": conf, "confidence_explanation": committee["confidence_explanation"],
            "policy_recommendation": committee["policy_recommendation"], "overridden": committee["overridden"],
            "override_reason": committee["override_reason"], "allowed_recommendations": committee["allowed"],
            "gates": committee["gates"], "decision_trace": committee["policy_trace"],
            "executive_summary": summary, "thesis": [c.model_dump() for c in thesis], "risks": risks,
            "entry_price": entry, "allocation": alloc,
            "monitoring_plan": {"triggers": triggers, "invalidation_conditions": inval or [],
                                "immediate_reassessment": ["High-materiality 8-K item", "Thesis assumption status 'Invalidated'",
                                                           "Restatement or non-reliance notice"],
                                "qualitative_assumptions": []},
            "scores": {"fundamental": fund.score.get("score") if fund and fund.score else None,
                       "business_quality": q, "valuation": v, "technical": committee["scores"].get("technical"),
                       "news": committee["scores"].get("news"), "risk": rscore, "risk_rating": risk_rating(rscore),
                       "direction": "all scores 0–10, higher = more favourable (risk: higher = lower risk)"},
            "mode": "deterministic", "narrative_model_run": None,
        }
        if llm is not None:
            allowed = ctx.pack.ids()
            key = [i for i in ctx.pack.items.values() if i.category in ("assessment", "scenario", "calendar")]
            user = (f"Decision (fixed): {rec}, confidence {conf:.0%}. Committee rationale: {committee['rationale']}\n"
                    f"Why not alternatives: {committee['why_not_alternatives']}\nEntry price: {entry.get('text')}\n"
                    f"Allocation: {alloc.get('text')}\nRule-based summary: {summary}\nTop risks: {risks}\n"
                    f"Analyst summaries: {[(r.title, r.summary) for r in ctx.reports.values()]}\n\nEvidence:\n{render_for_prompt(key)}")
            try:
                res = llm.structured(load_prompt("common")[0] + "\n\n" + load_prompt("final_decision")[0], user, FinalNarrative)
                data, _ = strip_invalid(res.parsed, allowed)
                report["executive_summary"] = data["executive_summary"]
                report["thesis"] = data["thesis"] or report["thesis"]
                report["risks_narrative"] = data["top_risks"]
                report["monitoring_plan"]["triggers"] += data["monitoring_triggers"]
                report["monitoring_plan"]["qualitative_assumptions"] = data["qualitative_assumptions"]
                report["mode"] = "llm"
                report["unverified_numbers"] = unverified_numbers(claim_texts(data), list(ctx.pack.items.values()))
                ctx.model_runs.append({"agent": "final_decision", "provider": res.provider, "model": res.model,
                                       "prompt_name": "final_decision", "prompt_version": load_prompt("final_decision")[1],
                                       "prompt_sha256": load_prompt("final_decision")[2], "input_tokens": res.input_tokens,
                                       "output_tokens": res.output_tokens, "latency_ms": res.latency_ms, "status": "ok",
                                       "stop_reason": res.stop_reason, "guardrail_violations": []})
            except LLMError as exc:
                report["mode"] = "deterministic_fallback"
                report["narrative_error"] = str(exc)
        report["meta"] = {
            "analysis_id": ctx.analysis_id, "generated_at": datetime.now(UTC).isoformat(),
            "price_date": ctx.data_through.get("prices"), "reference_price": snap_price,
            "financials_through": ctx.data_through.get("financials"), "horizon": ctx.params.get("horizon"),
            "benchmark": ctx.params.get("benchmark"), "data_quality": ctx.dq.status, "versions": versions(),
            "sources": _sources(ctx),
            "assumptions": [f"Business type classified as {ctx.profile.get('primary')} ({'; '.join(ctx.profile.get('rationale', []))})",
                            f"Peer group: {ctx.peer.get('level')} '{ctx.peer.get('name')}' ({ctx.peer.get('n_with_data')} with data)",
                            "Valuation uses latest close and TTM figures; forward figures are vendor consensus estimates.",
                            "Price history from Yahoo Finance is split-adjusted; total returns use adjusted closes."],
            "limitations": [
                "yfinance is an unofficial Yahoo Finance client: not a licensed feed, may change without notice; commercial use restricted.",
                "Peer comparisons use vendor-reported metrics for all peers; definitions may differ slightly from our calculations.",
                "News coverage is limited to the Yahoo feed and SEC filings.",
                "Scores and thresholds are analytical conventions, not facts; they have not been statistically validated.",
            ] + committee.get("limitations", []),
            "price_display": fmt_value(snap_price, "USD/share") if snap_price else None,
        }
        emit("final_decision", "completed", f"Final decision: {rec} ({conf:.0%} confidence)", agent="final_decision")
        return report
