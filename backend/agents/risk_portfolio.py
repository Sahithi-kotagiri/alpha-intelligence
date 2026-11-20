"""Risk & Portfolio Analyst: standalone downside risk and portfolio impact."""

from __future__ import annotations

from typing import Any

from backend.agents.base import AgentContext, BaseAgent, assessment_claim, confidence_from
from backend.analysis.evidence import scoped
from backend.analysis.scoring import risk_rating
from backend.schemas.agents import AgentReportOut, Claim, RiskNarrative
from backend.tools.scenario_analysis import market_stress
from backend.tools.types import fmt_value


class RiskPortfolioAnalyst(BaseAgent):
    name = "risk_portfolio"
    title = "Risk & Portfolio Analyst"
    responsibility = "Standalone downside risk and the effect of adding the stock to the portfolio."
    prompt_name = "risk_portfolio"
    schema = RiskNarrative

    def scope(self, ctx: AgentContext):
        return scoped(ctx.pack, "risk_portfolio")

    def deterministic(self, ctx: AgentContext) -> dict[str, Any]:
        r = ctx.risk or {}
        s = ctx.scores.get("risk")
        if not r or s is None:
            return {"score": None, "summary": "Price history unavailable; risk metrics not computed.",
                    "missing": ["daily price history"], "confidence": ("Low", "No price data."), "tables": {}}
        rating = risk_rating(s.score)
        weaknesses: list[Claim] = []
        if r.get("max_drawdown_3y") is not None and r["max_drawdown_3y"] < -0.35:
            weaknesses.append(Claim(text=f"3-year maximum drawdown {fmt_value(r['max_drawdown_3y'], 'pct')}.", kind="fact",
                                    evidence_ids=["risk.max_drawdown_3y"]))
        if r.get("beta_2y") is not None and r["beta_2y"] > 1.3:
            weaknesses.append(Claim(text=f"2-year beta {r['beta_2y']:.2f} vs benchmark: amplified market moves.",
                                    kind="fact", evidence_ids=["risk.beta_2y"]))
        for k in ("debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "altman_z"):
            a = ctx.assessment(k)
            if a and a.verdict == "unfavourable":
                weaknesses.append(assessment_claim(a))
        strengths = []
        for k in ("interest_coverage", "net_debt_to_ebitda", "altman_z"):
            a = ctx.assessment(k)
            if a and a.verdict == "favourable":
                strengths.append(assessment_claim(a))
        pf = ctx.portfolio or {}
        missing = []
        alloc = pf.get("allocation") or {"available": False,
                                         "reason": "No portfolio supplied: allocation requires portfolio size, risk tolerance and horizon."}
        if not pf.get("available"):
            missing.append("Portfolio holdings not supplied: portfolio impact and allocation cannot be assessed.")
        stress = market_stress(r.get("beta_2y"))
        summary = (f"Risk rating {rating} (risk score {s.score}/10, higher = lower risk). 1y volatility "
                   f"{fmt_value(r.get('vol_1y'), 'pct')}, 3y max drawdown {fmt_value(r.get('max_drawdown_3y'), 'pct')}, "
                   f"2y beta {r.get('beta_2y') and round(r['beta_2y'], 2)} vs {ctx.params.get('benchmark')}.")
        if pf.get("available"):
            imp = pf.get("impact") or {}
            summary += (f" Adding at {fmt_value(imp.get('candidate_weight'), 'pct')} changes portfolio volatility from "
                        f"{fmt_value(imp.get('vol_before'), 'pct')} to {fmt_value(imp.get('vol_after'), 'pct')}.")
        tables = {"risk_rating": rating, "metrics": {k: r.get(k) for k in ("vol_1y", "vol_3y", "beta_1y", "beta_2y",
                                                                           "beta_obs", "max_drawdown_1y", "max_drawdown_3y",
                                                                           "max_drawdown_5y", "var_95_1d", "cvar_95_1d",
                                                                           "upside_capture", "downside_capture",
                                                                           "avg_dollar_volume_20d", "correlation_1y")},
                  "gaps": r.get("gaps"), "scenarios": ctx.scenarios, "market_stress": stress, "portfolio": pf,
                  "allocation": alloc, "beta_method": "OLS on daily returns, 504 sessions, vs benchmark",
                  "stop_loss_considerations": ("A stop-loss can limit losses in orderly markets but does not eliminate risk: "
                                               f"opening gaps (largest in 2y: {fmt_value((r.get('gaps') or {}).get('largest_gap'), 'pct')}) "
                                               "can execute far below the stop, and tight stops relative to ATR cause whipsaw exits.")}
        return {"score": s, "summary": summary, "strengths": strengths, "weaknesses": weaknesses, "missing": missing,
                "tables": tables, "confidence": confidence_from(s.coverage, ctx.dq.status)}

    def context_text(self, ctx: AgentContext, det: dict[str, Any]) -> str:
        s = ctx.scores.get("risk")
        return (f"Risk score {s.score if s else None}/10 (higher = lower risk): {s.explanation if s else ''}. "
                f"Allocation calculation: {det['tables'].get('allocation')}. Market stress (beta-based): {det['tables'].get('market_stress')}.")

    def apply_narrative(self, report: AgentReportOut, n: dict[str, Any], ctx: AgentContext) -> None:
        super().apply_narrative(report, n, ctx)
        report.weaknesses = [Claim(text=f"{x['risk']} (likelihood {x['likelihood']}, impact {x['impact']})", kind="interpretation",
                                   evidence_ids=x["evidence_ids"]) for x in n.get("main_risks", [])]
        report.claims = [Claim(**n["scenario_commentary"])]
        report.deterministic["llm_allocation_commentary"] = n.get("allocation_commentary")
        report.deterministic["llm_stop_loss_considerations"] = n.get("stop_loss_considerations")
        report.deterministic["llm_portfolio_commentary"] = n.get("portfolio_commentary")
        report.summary = report.summary or n.get("allocation_commentary", "")
