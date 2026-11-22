"""Valuation Analyst: fair value, margin of safety and what the price implies.

Deterministic core (backend/analysis/factors.py): a 10-year DCF on FCF after stock-based
compensation at capped 5-year revenue growth, TTM EPS × own median P/E, a reverse DCF for
the growth implied by today's enterprise value, and P/E against the company's own history.
The LLM interprets these numbers; it cannot change them.
"""

from __future__ import annotations

from typing import Any

from backend.agents.base import AgentContext, BaseAgent
from backend.analysis.evidence import scoped
from backend.analysis.scoring import DimensionScore, Score
from backend.schemas.agents import AgentReportOut, Claim, ValuationNarrative


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


class ValuationAnalyst(BaseAgent):
    name = "valuation"
    title = "Valuation Analyst"
    responsibility = "Fair value, buy-below price, growth implied by the price, and P/E versus the company's own history."
    prompt_name = "valuation"
    schema = ValuationNarrative

    def scope(self, ctx: AgentContext):
        return scoped(ctx.pack, "valuation")

    def deterministic(self, ctx: AgentContext) -> dict[str, Any]:
        rb = ctx.rulebook or {}
        pil = (rb.get("pillars") or {}).get("value") or {}
        price, fv, bb = rb.get("price"), rb.get("fair_value"), rb.get("buy_below")
        ig, g = rb.get("implied_growth"), rb.get("revenue_cagr")
        strengths, weaknesses, claims, missing = [], [], [], []
        if fv and price:
            gap = price / fv - 1
            c = Claim(text=f"Price ${price:,.2f} is {abs(gap):.0%} {'below' if gap < 0 else 'above'} the rulebook fair value "
                           f"${fv:,.2f}; buy-below ${bb:,.2f}.", kind="interpretation",
                      evidence_ids=["rb.fair_value", "rb.buy_below"] + (["rb.price_vs_fair"] if rb.get("price_vs_fair") else []))
            (strengths if price <= fv else weaknesses).append(c)
            for m in rb.get("fair_value_methods", []):
                claims.append(Claim(text=f"{m['method']}: ${m['value']:,.2f}.", kind="estimate", evidence_ids=["rb.fair_value"]))
        else:
            missing.append("Fair value not computable: needs positive FCF after SBC with revenue history, or positive EPS "
                           "with 3+ years of P/E history.")
        if ig is not None and g is not None:
            c = Claim(text=f"Today's price implies about {_pct(ig)} annual FCF growth for 10 years; the company achieved "
                           f"{_pct(g)} revenue growth (CAGR).", kind="interpretation",
                      evidence_ids=["rb.implied_growth", "rb.revenue_cagr"])
            (strengths if g >= ig else weaknesses).append(c)
        for mm in pil.get("measures", []):
            if mm.get("score") is None:
                if not mm.get("not_applicable"):
                    missing.append(f"{mm['label']}: {mm.get('reason')}")
                continue
            if mm["key"] == "growth_gap":
                continue
            c = Claim(text=f"{mm['label']}: score {mm['score']:.0f}/100 ({mm['basis']}).", kind="fact",
                      evidence_ids=[f"rb.m.{mm['key']}"])
            if mm["score"] >= 65:
                strengths.append(c)
            elif mm["score"] <= 35:
                weaknesses.append(c)
            else:
                claims.append(c)
        sc = pil.get("score")
        dims = [DimensionScore(name=mm["key"], weight=1.0, score=None if mm.get("score") is None else round(mm["score"] / 10, 2),
                               coverage=1.0 if mm.get("score") is not None else 0.0, components=[mm]) for mm in pil.get("measures", [])]
        score = Score(name="valuation", score=None if sc is None else round(sc / 10, 2), rubric_version=rb.get("rulebook_version", ""),
                      dimensions=dims, coverage=1 - (pil.get("missing_ratio") or 0),
                      explanation="Value pillar of the rulebook (sector percentile 0–100, shown here ÷10): FCF yield, "
                                  "achieved minus implied growth, and P/E percentile against own 10-year history.")
        conf = ("High", "All value measures available.") if not missing else (
            ("Medium", "Some value measures unavailable.") if fv else ("Low", "No fair value could be computed."))
        summary = (f"Value pillar {sc:.0f}/100. " if sc is not None else "") + (
            f"Fair value ${fv:,.2f} vs price ${price:,.2f}; buy-below ${bb:,.2f}." if fv and price else
            "Fair value not computable from available data.")
        return {"score": score, "summary": summary, "strengths": strengths, "weaknesses": weaknesses, "claims": claims,
                "missing": missing, "confidence": conf,
                "tables": {"fair_value": fv, "buy_below": bb, "price": price, "methods": rb.get("fair_value_methods", []),
                           "implied_growth": ig, "revenue_cagr": g, "value_pillar": pil}}

    def context_text(self, ctx: AgentContext, det: dict[str, Any]) -> str:
        t = det["tables"]
        return (f"Rulebook valuation: price {t['price']}, fair value {t['fair_value']}, buy-below {t['buy_below']}, "
                f"methods {t['methods']}, implied growth {t['implied_growth']}, achieved revenue CAGR {t['revenue_cagr']}. "
                f"Value pillar: {t['value_pillar'].get('score')} with measures {t['value_pillar'].get('measures')}.")

    def apply_narrative(self, report: AgentReportOut, n: dict[str, Any], ctx: AgentContext) -> None:
        super().apply_narrative(report, n, ctx)
        report.claims = [Claim(**n[k]) for k in ("fair_value_view", "market_expectations", "pe_history_view",
                                                 "margin_of_safety_view") if n.get(k)] + \
            [Claim(**c) for c in n.get("key_sensitivities", [])]

    def deterministic_review(self, ctx: AgentContext, question: str) -> dict[str, Any]:
        rb = ctx.rulebook or {}
        if not rb.get("fair_value"):
            return super().deterministic_review(ctx, question)
        return {"answer": f"Rule-based: fair value ${rb['fair_value']:,.2f} (methods {rb.get('fair_value_methods')}); price "
                          f"{rb.get('price')}; implied growth {_pct(rb.get('implied_growth'))} vs achieved "
                          f"{_pct(rb.get('revenue_cagr'))}.",
                "evidence_ids": ["rb.fair_value", "rb.implied_growth", "rb.revenue_cagr"],
                "resolved": True, "cannot_resolve_reason": "", "mode": "deterministic"}
