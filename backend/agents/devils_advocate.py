"""Devil's Advocate: independently argues against buying, citing evidence.

Rule-based objections are derived from unfavourable assessments, trend, events, data
gaps and concentration. With an LLM, the agent also reviews the other analysts'
reports for weak assumptions and overlooked evidence. A thesis-failure probability is
only given by the LLM and always labelled as an uncalibrated expert-judgement range.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from backend.agents.base import AgentContext, BaseAgent
from backend.analysis.evidence import render_for_prompt
from backend.schemas.agents import AgentReportOut, Claim, DevilsAdvocateNarrative
from backend.tools.financial_metrics import latest_metric  # noqa: F401  (kept for extension)
from backend.tools.types import fmt_value

VAL_KEYS = ["trailing_pe", "forward_pe", "peg_forward", "peg_trailing", "ev_to_ebitda", "price_to_fcf", "pe_vs_own_history",
            "fcf_yield", "ev_to_sales"]


def rule_objections(ctx: AgentContext) -> list[dict[str, Any]]:
    by = {a.metric_key: a for a in ctx.assessments}
    obj: list[dict[str, Any]] = []

    def unf(*keys):
        return [by[k] for k in keys if k in by and by[k].verdict == "unfavourable"]

    v = unf(*VAL_KEYS)
    if v:
        obj.append({"objection": "Valuation leaves a limited margin of safety: "
                                 + "; ".join(f"{a.label} {a.display}" for a in v[:3]) + ".",
                    "evidence_ids": [f"asm.{a.metric_key}" for a in v],
                    "mechanism": "If growth or margins disappoint, the multiple can compress toward the peer median, causing "
                                 "losses even if the business performs adequately.",
                    "likelihood": "medium", "severity": "high" if len(v) >= 2 else "medium",
                    "what_would_weaken_it": "Earnings growth that brings multiples back toward peer medians, or a lower price.",
                    "verify_before_deciding": "Whether consensus growth is credible given recent quarterly growth and guidance."})
    gp = unf("revenue_yoy_quarterly", "revenue_yoy_annual", "diluted_eps_yoy_quarterly", "revenue_cagr_3y")
    if gp:
        obj.append({"objection": "Growth trails the reference group: " + "; ".join(f"{a.label} {a.display}" for a in gp) + ".",
                    "evidence_ids": [f"asm.{a.metric_key}" for a in gp],
                    "mechanism": "Slower growth than peers can justify a lower multiple and signals share loss or market maturity.",
                    "likelihood": "medium", "severity": "medium",
                    "what_would_weaken_it": "Growth converging to or exceeding the peer median.",
                    "verify_before_deciding": "Whether peer growth is driven by smaller bases or acquisitions."})
    pm = unf("gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm", "roic_annual", "roe_ttm")
    if pm:
        obj.append({"objection": "Profitability or returns below the reference group: "
                                 + "; ".join(f"{a.label} {a.display}" for a in pm) + ".",
                    "evidence_ids": [f"asm.{a.metric_key}" for a in pm],
                    "mechanism": "Weaker unit economics leave less room to absorb competition or cost pressure.",
                    "likelihood": "medium", "severity": "low" if len(pm) == 1 else "medium",
                    "what_would_weaken_it": "Business-mix differences that explain the gap (e.g. hardware vs software).",
                    "verify_before_deciding": "Segment mix versus the peer group."})
    g_q, g_3 = by.get("revenue_yoy_quarterly"), by.get("revenue_cagr_3y")
    if g_q and g_3 and g_q.value is not None and g_3.value is not None and g_q.value < g_3.value - 0.10:
        obj.append({"objection": f"Revenue growth is decelerating: latest quarter YoY {g_q.display} vs 3-year CAGR {g_3.display}.",
                    "evidence_ids": [f"asm.{g_q.metric_key}", f"asm.{g_3.metric_key}"],
                    "mechanism": "Valuations anchored on historical growth re-rate when growth slows.",
                    "likelihood": "medium", "severity": "medium",
                    "what_would_weaken_it": "Re-acceleration in the next one or two quarters.",
                    "verify_before_deciding": "Whether the slowdown is cyclical, comparison-driven or structural."})
    om, avg, sd = by.get("operating_margin_ttm"), latest_metric_val(ctx, "operating_margin_avg"), latest_metric_val(ctx, "operating_margin_stdev")
    if om and om.value is not None and avg is not None and sd is not None and om.value < avg - sd:
        obj.append({"objection": f"Operating margin {om.display} is below its multi-year average by more than one standard deviation.",
                    "evidence_ids": [f"asm.{om.metric_key}", "met.operating_margin_annual_avg5"],
                    "mechanism": "Margin erosion can signal pricing pressure or cost inflation.", "likelihood": "medium",
                    "severity": "medium", "what_would_weaken_it": "Margin recovery in subsequent quarters.",
                    "verify_before_deciding": "Drivers of the margin decline (mix, one-offs or competition)."})
    cf = unf("fcf_margin_ttm", "fcf_conversion_annual", "free_cash_flow_yoy_annual")
    if cf:
        obj.append({"objection": "Cash generation is weak relative to peers or reported earnings: "
                                 + "; ".join(f"{a.label} {a.display}" for a in cf) + ".",
                    "evidence_ids": [f"asm.{a.metric_key}" for a in cf],
                    "mechanism": "Earnings not converted to cash limit buybacks, dividends and debt reduction, and can "
                                 "indicate aggressive accounting or heavy reinvestment needs.",
                    "likelihood": "medium", "severity": "medium",
                    "what_would_weaken_it": "Improving FCF conversion over the next year.",
                    "verify_before_deciding": "Whether capex is growth investment with credible returns."})
    lev = unf("debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "altman_z", "current_ratio")
    if lev:
        obj.append({"objection": "Balance-sheet risk: " + "; ".join(f"{a.label} {a.display}" for a in lev) + ".",
                    "evidence_ids": [f"asm.{a.metric_key}" for a in lev],
                    "mechanism": "High leverage magnifies downturns and refinancing at higher rates reduces equity value.",
                    "likelihood": "medium", "severity": "high" if len(lev) >= 2 else "medium",
                    "what_would_weaken_it": "Deleveraging or demonstrated stable cash flows covering interest comfortably.",
                    "verify_before_deciding": "Debt maturity schedule (not available in current data sources)."})
    dil = unf("share_change_1y")
    if dil:
        obj.append({"objection": f"Shareholder dilution: {dil[0].label} {dil[0].display}.",
                    "evidence_ids": [f"asm.{dil[0].metric_key}"], "mechanism": "Per-share value grows slower than the business.",
                    "likelihood": "high", "severity": "low", "what_would_weaken_it": "Buybacks offsetting issuance.",
                    "verify_before_deciding": "Stock-based compensation trend."})
    q = unf("piotroski_f_score", "accruals_ratio_annual")
    if q:
        obj.append({"objection": "Earnings-quality flags: " + "; ".join(f"{a.label} {a.display}" for a in q) + ".",
                    "evidence_ids": [f"asm.{a.metric_key}" for a in q],
                    "mechanism": "Low-quality earnings are more likely to be revised down.", "likelihood": "medium",
                    "severity": "medium", "what_would_weaken_it": "Cash-backed earnings in coming quarters.",
                    "verify_before_deciding": "Composition of accruals and one-off items."})
    t = ctx.technicals or {}
    if t.get("trend_long") == "down" or (t.get("trend_medium") == "down" and t.get("trend_short") == "down"):
        obj.append({"objection": f"Price trend is negative (short/medium/long: {t.get('trend_short')}/{t.get('trend_medium')}/{t.get('trend_long')}).",
                    "evidence_ids": ["tech.trend_short", "tech.trend_medium", "tech.trend_long"],
                    "mechanism": "Buying into a downtrend risks further declines before fundamentals are recognised.",
                    "likelihood": "medium", "severity": "medium",
                    "what_would_weaken_it": "Price recovering above its 50- and 200-day averages.",
                    "verify_before_deciding": "Whether the decline reflects company-specific news."})
    ts = ctx.scores.get("technical")
    if ts is not None and ts.score is not None and ts.score < 5 and not (t.get("trend_long") == "down"):
        obj.append({"objection": f"The technical setup does not confirm entry (technical score {ts.score:.1f}/10).",
                    "evidence_ids": ["tech.trend_short", "tech.trend_medium", "tech.rsi_14"],
                    "mechanism": "Weak momentum or relative strength often persists for weeks; entering early risks drawdown.",
                    "likelihood": "medium", "severity": "low",
                    "what_would_weaken_it": "Improving relative strength and momentum.",
                    "verify_before_deciding": "Relative strength versus benchmark and sector."})
    today = date.today()
    for f in ctx.pack.by_category("filing"):
        if f.detail.get("materiality") == "high" and f.date and (today - date.fromisoformat(f.date)).days <= 180:
            obj.append({"objection": f"Recent high-materiality SEC filing: {f.title}.", "evidence_ids": [f.id],
                        "mechanism": "Items such as impairments, restatement notices, auditor changes or cyber incidents can "
                                     "change expected cash flows or risk.", "likelihood": "medium", "severity": "high",
                        "what_would_weaken_it": "Filing content showing limited financial impact.",
                        "verify_before_deciding": "Read the filing and assess its financial impact."})
    for c in ctx.pack.by_category("calendar"):
        try:
            d = date.fromisoformat(str(c.date)[:10])
        except ValueError:
            continue
        if 0 <= (d - today).days <= 30:
            obj.append({"objection": f"Earnings expected within 30 days ({d}); results can move the price sharply.",
                        "evidence_ids": [c.id], "mechanism": "Event/gap risk around results.", "likelihood": "high",
                        "severity": "low", "what_would_weaken_it": "Waiting for the results.",
                        "verify_before_deciding": "Whether entering before results is consistent with the risk budget."})
    if ctx.dq.status in ("poor", "critical") or ctx.dq.missing_critical_fields or ctx.dq.conflicts:
        obj.append({"objection": f"Evidence quality is {ctx.dq.status}: missing critical fields {ctx.dq.missing_critical_fields or 'none'}, "
                                 f"{len(ctx.dq.conflicts)} source conflicts.",
                    "evidence_ids": ["dq.report"], "mechanism": "Conclusions may rest on incomplete or inconsistent data.",
                    "likelihood": "high", "severity": "high" if ctx.dq.status == "critical" else "medium",
                    "what_would_weaken_it": "Verified figures from primary filings.",
                    "verify_before_deciding": "Resolve conflicts against the 10-K/10-Q."})
    gov = ctx.pack.get("vendor.governance")
    if gov and (gov.detail.get("overallRisk") or 0) >= 8:
        obj.append({"objection": f"Elevated governance risk score (overall {gov.detail.get('overallRisk')}/10).",
                    "evidence_ids": ["vendor.governance"], "mechanism": "Weak governance raises the risk of value-destructive "
                    "decisions.", "likelihood": "low", "severity": "medium",
                    "what_would_weaken_it": "Board or compensation changes.", "verify_before_deciding": "Proxy statement (DEF 14A)."})
    pf = (ctx.portfolio or {}).get("impact") or {}
    if pf.get("candidate_corr_to_portfolio") is not None and pf["candidate_corr_to_portfolio"] > 0.7:
        obj.append({"objection": f"High correlation with the existing portfolio ({pf['candidate_corr_to_portfolio']:.2f}): "
                                 "limited diversification benefit.", "evidence_ids": ["pf.candidate_corr_to_portfolio"],
                    "mechanism": "Correlated holdings fall together in drawdowns.", "likelihood": "high", "severity": "medium",
                    "what_would_weaken_it": "Smaller position or funding from correlated holdings.",
                    "verify_before_deciding": "Overlap in business drivers with current holdings."})
    return obj


def latest_metric_val(ctx: AgentContext, key: str) -> float | None:
    for i in ctx.pack.by_category("metric"):
        if i.detail.get("key") == key and i.value is not None:
            return i.value
    return None


class DevilsAdvocate(BaseAgent):
    name = "devils_advocate"
    title = "Devil's Advocate"
    responsibility = "Independently argues against buying; exposes weak assumptions and overlooked evidence."
    prompt_name = "devils_advocate"
    schema = DevilsAdvocateNarrative

    def scope(self, ctx: AgentContext):
        return [i for i in ctx.pack.items.values() if not (i.category == "financial" and "Q" in i.id.split(".")[-1][:1])]

    def deterministic(self, ctx: AgentContext) -> dict[str, Any]:
        obj = rule_objections(ctx)
        high = [o for o in obj if o["severity"] == "high"]
        weaknesses = [Claim(text=o["objection"], kind="interpretation", evidence_ids=o["evidence_ids"]) for o in obj]
        inval = []
        t = ctx.technicals or {}
        if t.get("sma_200"):
            inval.append(f"Sustained close below the 200-day average (${t['sma_200']:,.2f}).")
        g = ctx.assessment("revenue_yoy_quarterly")
        if g and g.value is not None:
            inval.append(f"Quarterly revenue growth falling below half its current level ({fmt_value(g.value / 2, 'pct')}).")
        om = ctx.assessment("operating_margin_ttm")
        if om and om.value is not None:
            inval.append(f"TTM operating margin falling more than 5 percentage points below {om.display}.")
        summary = (f"{len(obj)} rule-based objections ({len(high)} high severity). "
                   + (f"Strongest: {high[0]['objection']}" if high else (f"Strongest: {obj[0]['objection']}" if obj else
                      "No rule-based objection triggered; this does not mean the thesis is risk-free.")))
        tables = {"objections": obj, "invalidation_conditions": inval,
                  "thesis_failure": {"definition": f"Underperforming {ctx.params.get('benchmark')} by more than 15% over "
                                                   f"{ctx.params.get('horizon')}, or a monitored thesis assumption becoming invalid.",
                                     "estimate": None,
                                     "basis": "Not estimated in rule-based mode: no calibrated model exists, and an "
                                              "uncalibrated number would imply false precision."},
                  "high_severity_count": len(high)}
        conf = ("Medium", "Objections follow documented rules over the evidence pack.")
        return {"score": None, "summary": summary, "weaknesses": weaknesses, "claims": [], "missing": [],
                "tables": tables, "confidence": conf}

    def build_user_prompt(self, ctx: AgentContext, items, det) -> str:
        reports = "\n\n".join(
            f"## {r.title} (mode {r.mode}, score {r.score.get('score') if r.score else 'n/a'})\nSummary: {r.summary}\n"
            f"Strengths: {[c.text for c in r.strengths][:6]}\nWeaknesses: {[c.text for c in r.weaknesses][:6]}"
            for r in ctx.reports.values())
        return (f"Company: {ctx.company_name} ({ctx.ticker}). Horizon: {ctx.params.get('horizon')}. Benchmark: "
                f"{ctx.params.get('benchmark')}.\n\nPreliminary analyst reports:\n{reports}\n\nRule-based objections already "
                f"identified (you may refine, add or reject them with reasons):\n{det['tables']['objections']}\n\n"
                f"Evidence ({len(items)} items):\n{render_for_prompt(items)}")

    def apply_narrative(self, report: AgentReportOut, n: dict[str, Any], ctx: AgentContext) -> None:
        report.summary = n.get("bear_case_conclusion", report.summary)
        report.weaknesses = [Claim(text=o["objection"], kind="interpretation", evidence_ids=o["evidence_ids"])
                             for o in n.get("objections", [])]
        report.claims = [Claim(**c) for c in n.get("weak_assumptions", []) + n.get("overlooked_evidence", [])]
        tf = n.get("thesis_failure") or {}
        tf["label"] = "Uncalibrated expert-judgement estimate (LLM); not a statistical probability."
        report.deterministic["llm_objections"] = n.get("objections", [])
        report.deterministic["llm_invalidation_conditions"] = n.get("invalidation_conditions", [])
        report.deterministic["thesis_failure"] = tf
        report.deterministic["high_severity_count"] = sum(1 for o in n.get("objections", [])
                                                          if o["severity"] == "high" and o["likelihood"] != "low")
        report.confidence = "Medium"
        report.confidence_reason = "Bear case grounded in cited evidence; probability range is uncalibrated."
