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
from backend.analysis import factors as F
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
        da = ctx.reports.get("devils_advocate")
        rb = committee.get("rulebook") or ctx.rulebook
        entry = D.entry_price(rec, rb, ctx.data_through.get("prices"),
                              (tech.deterministic.get("entry_zone") if tech else {}) or {})
        d = rb.get("detail", {})
        size = F.position_size(rec, d.get("vol_1y"), ctx.bench_vol, d.get("max_drawdown_3y"))
        alloc = D.allocation(rec, size, ctx.portfolio)
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
        triggers += list(rb.get("review_triggers", []))
        triggers += ["Any new 10-Q/10-K filing (re-test thesis assumptions).",
                     "8-K items with high materiality (impairment, non-reliance, auditor change, cybersecurity, delisting).",
                     "Price move of more than 15% from the reference price or a close below the technical invalidation level."]
        inval = (da.deterministic.get("llm_invalidation_conditions") or da.deterministic.get("invalidation_conditions")) if da else []
        q, v = ctx.scores["fundamental_quality"].score, ctx.scores["valuation"].score
        rscore = ctx.scores["risk"].score if ctx.scores.get("risk") else None
        pil = {k: p["score"] for k, p in rb["pillars"].items()}
        fv_txt = (f"Fair value ${rb['fair_value']:,.2f}, buy-below ${rb['buy_below']:,.2f}, price ${rb['price']:,.2f}. "
                  if rb.get("fair_value") and rb.get("price") else "Fair value not computable. ")
        summary = (f"{ctx.company_name} ({ctx.ticker}) — {rec} at {conf:.0%} confidence (evidence quality, not probability of "
                   f"profit). Rulebook composite {rb['composite']:.0f}/100: quality {pil['quality']:.0f}, value "
                   f"{pil['value']:.0f}, momentum {pil['momentum']:.0f}, revisions {pil['revisions']:.0f} (sector "
                   f"percentiles). {fv_txt}"
                   f"Decision basis: {committee['policy_trace'][-1] if committee['policy_trace'] else ''} "
                   + (f"Strongest objection: {risks[0]['text']} " if risks else "")
                   + f"Data quality: {ctx.dq.status}; prices through {ctx.data_through.get('prices')}, financials through "
                   f"{ctx.data_through.get('financials')}.")
        da_det = (da.deterministic if da else {}) or {}
        rulebook_view = {
            "version": rb["rulebook_version"], "label": rec, "rulebook_label": committee["policy_recommendation"],
            "composite": rb["composite"], "pillars": rb["pillars"], "trace": committee["policy_trace"],
            "gates": committee["gates"], "price": rb.get("price"), "fair_value": rb.get("fair_value"),
            "buy_below": rb.get("buy_below"), "price_vs_fair": rb.get("price_vs_fair"),
            "fair_value_methods": rb.get("fair_value_methods", []), "implied_growth": rb.get("implied_growth"),
            "fair_value_low_precision": bool(d.get("fair_value_low_precision")), "fair_value_spread": d.get("fair_value_spread"),
            "revenue_cagr": rb.get("revenue_cagr"), "position_size": size, "data_date": ctx.data_through.get("prices"),
            "reasons_for": rb.get("reasons_for", []), "reasons_against": rb.get("reasons_against", []),
            "what_would_change": rb.get("what_would_change", []), "confidence": conf,
            "confidence_reasons": committee["confidence_explanation"], "review_triggers": rb.get("review_triggers", []),
            "market_belief": da_det.get("market_belief"), "our_difference": da_det.get("our_difference"),
            "difference_credible": da_det.get("difference_credible"), "top_risks": da_det.get("top_risks", []),
            "peer_count": rb.get("peer_count"), "missing": rb.get("missing", {}),
            "insufficient_data": committee.get("insufficient_data", False),
        }
        report: dict[str, Any] = {
            "recommendation": rec, "confidence": conf, "confidence_explanation": committee["confidence_explanation"],
            "policy_recommendation": committee["policy_recommendation"], "overridden": committee["overridden"],
            "override_reason": committee["override_reason"], "allowed_recommendations": committee["allowed"],
            "gates": committee["gates"], "decision_trace": committee["policy_trace"], "rulebook": rulebook_view,
            "executive_summary": summary, "thesis": [c.model_dump() for c in thesis], "risks": risks,
            "entry_price": entry, "allocation": alloc,
            "monitoring_plan": {"triggers": triggers, "invalidation_conditions": inval or [],
                                "immediate_reassessment": ["High-materiality 8-K item", "Thesis assumption status 'Invalidated'",
                                                           "Restatement or non-reliance notice"],
                                "qualitative_assumptions": []},
            "scores": {"composite": rb["composite"], **{f"pillar_{k}": v for k, v in pil.items()},
                       "fundamental": fund.score.get("score") if fund and fund.score else None,
                       "business_quality": q, "valuation": v, "technical": committee["scores"].get("technical"),
                       "news": committee["scores"].get("news"), "risk": rscore, "risk_rating": risk_rating(rscore),
                       "direction": "composite and pillars 0–100 (sector percentiles); agent scores 0–10; higher = more "
                                    "favourable (risk: higher = lower risk)"},
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
        if ctx.params.get("reassessment"):
            report["reassessment"] = {"reasons": ctx.params.get("reassessment_reasons") or [],
                                      "items": ctx.params["reassessment"],
                                      "previous": ctx.params.get("prior")}
        report["meta"] = {
            "analysis_id": ctx.analysis_id, "generated_at": datetime.now(UTC).isoformat(),
            "price_date": ctx.data_through.get("prices"), "reference_price": snap_price,
            "financials_through": ctx.data_through.get("financials"), "horizon": ctx.params.get("horizon"),
            "benchmark": ctx.params.get("benchmark"), "data_quality": ctx.dq.status, "versions": versions(),
            "sources": _sources(ctx),
            "assumptions": [f"Business type classified as {ctx.profile.get('primary')} ({'; '.join(ctx.profile.get('rationale', []))})",
                            f"Peer group: {ctx.peer.get('level')} '{ctx.peer.get('name')}' ({ctx.peer.get('n_with_data')} with data)",
                            "Valuation uses latest close and TTM figures; forward figures are vendor consensus estimates.",
                            f"Rulebook {rb['rulebook_version']}: pillars ranked against {rb.get('peer_count') or 0} screened S&P 500 "
                            "companies (sector percentile, falling back to the S&P 500 or documented absolute anchors).",
                            "Price history from Yahoo Finance is split-adjusted; total returns use adjusted closes."],
            "limitations": [
                "yfinance is an unofficial Yahoo Finance client: not a licensed feed, may change without notice; commercial use restricted.",
                "Peer comparisons use vendor-reported metrics for all peers; definitions may differ slightly from our calculations.",
                "News coverage is limited to the Yahoo feed and SEC filings.",
                "Rulebook thresholds and weights are the team's analytical conventions, not facts; they have not been statistically validated.",
                "Fair value depends on the discount rate (9%), terminal growth (2.5%) and capped historical growth; it is a model estimate.",
            ] + committee.get("limitations", []),
            "price_display": fmt_value(snap_price, "USD/share") if snap_price else None,
        }
        emit("final_decision", "completed", f"Final decision: {rec} ({conf:.0%} confidence)", agent="final_decision")
        return report
