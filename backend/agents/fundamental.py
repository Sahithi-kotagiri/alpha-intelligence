"""Fundamental Analyst: business quality, financial health, moat evidence, valuation."""

from __future__ import annotations

from typing import Any

from backend.agents.base import AgentContext, BaseAgent, assessment_claim, confidence_from
from backend.analysis.evidence import scoped
from backend.analysis.scoring import Score
from backend.schemas.agents import AgentReportOut, Claim, FundamentalNarrative

RUBRIC_WEIGHT_ORDER = ["revenue_cagr_3y", "revenue_yoy_annual", "diluted_eps_cagr_3y", "operating_margin_ttm", "roic_annual",
                       "fcf_margin_ttm", "net_debt_to_ebitda", "debt_to_equity", "interest_coverage", "gross_margin_ttm",
                       "roe_ttm", "piotroski_f_score", "share_change_1y", "fcf_conversion_annual", "current_ratio",
                       "revenue_yoy_quarterly", "diluted_eps_yoy_quarterly", "net_margin_ttm", "altman_z",
                       "accruals_ratio_annual", "free_cash_flow_yoy_annual", "diluted_eps_yoy_annual"]
VALUATION_KEYS = ["trailing_pe", "forward_pe", "peg_forward", "peg_trailing", "ev_to_ebitda", "price_to_fcf",
                  "price_to_sales", "price_to_book", "fcf_yield", "pe_vs_own_history", "ev_to_sales"]


class FundamentalAnalyst(BaseAgent):
    name = "fundamental"
    title = "Fundamental Analyst"
    responsibility = "Business quality, financial health, durability, capital allocation and valuation."
    prompt_name = "fundamental"
    schema = FundamentalNarrative

    def scope(self, ctx: AgentContext):
        return scoped(ctx.pack, "fundamental")

    def deterministic(self, ctx: AgentContext) -> dict[str, Any]:
        q: Score = ctx.scores["fundamental_quality"]
        v: Score = ctx.scores["valuation"]
        by = {a.metric_key: a for a in ctx.assessments}
        order = RUBRIC_WEIGHT_ORDER
        strengths = [assessment_claim(by[k]) for k in order if k in by and by[k].verdict == "favourable"][:6]
        weaknesses = [assessment_claim(by[k]) for k in order if k in by and by[k].verdict == "unfavourable"][:6]
        val_claims = [assessment_claim(by[k]) for k in VALUATION_KEYS if k in by and by[k].verdict in
                      ("favourable", "unfavourable", "neutral")]
        missing = [f"{a.label}: {a.explanation}" for a in ctx.assessments
                   if a.verdict in ("insufficient_data",) and a.metric_key in order + VALUATION_KEYS][:10]
        conf = confidence_from(min(q.coverage, v.coverage), ctx.dq.status)
        combined = None
        if q.score is not None and v.score is not None:
            combined = round(0.65 * q.score + 0.35 * v.score, 2)
        qs = "n/a" if q.score is None else f"{q.score:.1f}"
        vs = "n/a" if v.score is None else f"{v.score:.1f}"
        summary = (f"Business-quality score {qs}/10 and valuation score {vs}/10 "
                   f"(business type: {ctx.profile.get('primary')}; peers: {ctx.peer.get('level')} '{ctx.peer.get('name')}', "
                   f"n={ctx.peer.get('n_with_data')}). {len(strengths)} favourable and {len(weaknesses)} unfavourable "
                   "quality metrics under the threshold rules.")
        score = q.model_copy(update={"name": "fundamental", "score": combined,
                                     "explanation": f"Fundamental score = 0.65 × business quality ({qs}) + 0.35 × valuation "
                                                    f"({vs}). Business quality: {q.explanation} Valuation: {v.explanation}"})
        tables = {"quality_score": q.model_dump(), "valuation_score": v.model_dump(),
                  "valuation_claims": [c.model_dump() for c in val_claims], "business_profile": ctx.profile,
                  "peer_group": ctx.peer}
        return {"score": score, "summary": summary, "strengths": strengths, "weaknesses": weaknesses,
                "claims": val_claims, "missing": missing, "tables": tables, "confidence": conf}

    def context_text(self, ctx: AgentContext, det: dict[str, Any]) -> str:
        q, v = ctx.scores["fundamental_quality"], ctx.scores["valuation"]
        return (f"Business profile: {ctx.profile}. Peer group: {ctx.peer.get('level')} '{ctx.peer.get('name')}' "
                f"({ctx.peer.get('n_with_data')} with data). Business-quality score {q.score}/10 ({q.explanation}). "
                f"Valuation score {v.score}/10 ({v.explanation}). Data quality: {ctx.dq.status}.")

    def apply_narrative(self, report: AgentReportOut, n: dict[str, Any], ctx: AgentContext) -> None:
        super().apply_narrative(report, n, ctx)
        report.strengths = [Claim(**c) for c in n.get("strengths", [])] or report.strengths
        report.weaknesses = [Claim(**c) for c in n.get("weaknesses", [])] or report.weaknesses
        report.claims = [Claim(**n["valuation_assessment"])] + [Claim(**c) for c in n.get("management_capital_allocation", [])] \
            + [Claim(**c) for c in n.get("competitor_context", [])]

    def deterministic_review(self, ctx: AgentContext, question: str) -> dict[str, Any]:
        ql = question.lower()
        keys = (["peg_forward", "peg_trailing", "revenue_cagr_3y", "diluted_eps_cagr_3y", "trailing_pe", "pe_vs_own_history"]
                if ("valuation" in ql or "premium" in ql or "growth" in ql) else
                ["fcf_margin_ttm", "fcf_conversion_annual", "free_cash_flow_yoy_annual", "operating_margin_ttm"]
                if ("cash" in ql or "financial" in ql) else
                ["debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "roic_annual"])
        found = [ctx.assessment(k) for k in keys]
        found = [a for a in found if a is not None]
        if not found:
            return super().deterministic_review(ctx, question)
        ans = " ".join(f"{a.label}: {a.display} → {a.verdict}." for a in found)
        decisive = [a for a in found if a.verdict in ("favourable", "unfavourable")]
        return {"answer": f"Rule-based evidence: {ans}", "evidence_ids": [f"asm.{a.metric_key}" for a in found],
                "resolved": bool(decisive), "cannot_resolve_reason": "" if decisive else "relevant metrics are neutral or unavailable",
                "mode": "deterministic"}
