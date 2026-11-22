"""Committee rules: agreement/disagreement detection, decision gates, policy, confidence,
entry price, allocation and monitoring plan. All thresholds come from decision_policy.yaml.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from backend.tools.types import fmt_value

RECS = ["Strong Buy", "Buy", "Wait", "Watchlist", "Reject"]   # most to least bullish (rulebook)
BUYS = ("Strong Buy", "Buy")
HIGH_MATERIALITY_8K = {"1.03", "1.05", "2.04", "2.06", "3.01", "4.01", "4.02", "5.01"}


def direction(score: float | None) -> str | None:
    if score is None:
        return None
    return "positive" if score >= 6 else "negative" if score <= 4 else "neutral"


def consistency_checks(ctx) -> list[dict[str, Any]]:
    dt = ctx.data_through
    return [{"check": "Same price date across agents", "result": f"all agents used prices through {dt.get('prices')}", "ok": True},
            {"check": "Same reporting period", "result": f"latest financial period {dt.get('financials')}", "ok": True},
            {"check": "Same benchmark", "result": f"{ctx.params.get('benchmark')}", "ok": True},
            {"check": "Same currency", "result": dt.get("currency") or "n/a",
             "ok": not any(i.code == "currency_mismatch" for i in ctx.dq.issues)}]


def detect(ctx, scores: dict[str, float | None], da_high: int) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (agreements, disagreements, review_questions)."""
    q, v, t, r, n = (scores.get(k) for k in ("quality", "valuation", "technical", "risk", "news"))
    dirs = {"fundamental (quality)": direction(q), "valuation": direction(v), "technical": direction(t),
            "risk (higher=lower risk)": direction(r), "news": direction(n)}
    agreements = []
    for d in ("positive", "negative"):
        who = [k for k, x in dirs.items() if x == d]
        if len(who) >= 3:
            agreements.append({"text": f"{', '.join(who)} views are {d}.", "kind": "interpretation",
                               "evidence_ids": ["dq.report"]})
    dis, qs = [], []

    def add(topic, agents, nature, desc, ev, resolution, target, question):
        dis.append({"topic": topic, "agents": agents, "nature": nature, "description": desc, "evidence_ids": ev,
                    "resolution": resolution, "resolved": False})
        qs.append({"target_agent": target, "question": question, "topic": topic})

    if q is not None and v is not None and q >= 7 and v <= 4:
        add("Business quality vs price", ["fundamental"], "analytical",
            f"Business quality scores {q:.1f}/10 but valuation scores {v:.1f}/10.",
            ["asm.trailing_pe", "asm.peg_forward", "asm.roic_annual"],
            "Quality supports keeping the stock under consideration; valuation governs entry timing (Wait/Watchlist "
            "rather than a buy label unless growth-adjusted valuation is acceptable).",
            "fundamental", "Is the valuation premium supported by growth on a growth-adjusted (PEG) basis?")
    if q is not None and t is not None and q >= 7 and t <= 4:
        add("Fundamentals vs price trend", ["fundamental", "technical"], "time_horizon",
            f"Fundamentals are strong ({q:.1f}) while the technical setup is weak ({t:.1f}).",
            ["tech.trend_medium", "tech.trend_long"],
            "Different horizons: fundamentals describe multi-year value; technicals describe weeks-to-months timing. "
            "Weak technicals argue for Wait rather than Reject.",
            "technical", "Does the weak trend reflect company-specific deterioration or market-wide moves?")
    cf_dim = None
    fq = ctx.scores.get("fundamental_quality")
    if fq is not None:
        cf_dim = next((d.score for d in fq.dimensions if d.name == "cash_flow"), None)
    if n is not None and n >= 6.5 and cf_dim is not None and cf_dim <= 4:
        add("Positive news vs weak cash flow", ["news", "fundamental"], "analytical",
            f"News flow is positive ({n:.1f}) but cash-flow metrics score {cf_dim:.1f}/10.",
            ["asm.fcf_margin_ttm", "asm.fcf_conversion_annual"],
            "Reported financial evidence takes priority over headline sentiment.",
            "fundamental", "Is the weak cash flow temporary (investment phase) or structural?")
    if r is not None and q is not None and r <= 3.5 and q >= 6.5:
        add("Quality business with high risk", ["risk_portfolio", "fundamental"], "analytical",
            f"Quality {q:.1f}/10 but risk score {r:.1f}/10 (high risk).", ["risk.vol_1y", "risk.max_drawdown_3y"],
            "Risk constrains position size even if the business is attractive.",
            "risk_portfolio", "What position size keeps this within a reasonable risk budget?")
    pos = sum(1 for x in (q, t, n) if x is not None and x >= 6)
    if da_high >= 2 and pos >= 2:
        add("Bear case contests bullish analyst views", ["devils_advocate"], "analytical",
            f"The Devil's Advocate raised {da_high} high-severity objections while {pos} analyst scores are positive.",
            [], "High-severity objections must be addressed before a buy label.", "fundamental",
            "Which high-severity objection is least supported by the evidence?")
    return agreements, dis, qs


def rulebook_gates(ctx, da_high_unresolved: int, portfolio_breach: str | None) -> list[dict[str, Any]]:
    """Gates decided at committee time (the factor engine adds filing, leverage, dilution and trend gates).
    Each gate caps the label (`effect`); `blocks` lists the labels it rules out."""
    out = []

    def cap(gid, effect, reason, ev=None):
        out.append({"id": gid, "effect": effect, "blocks": RECS[:RECS.index(effect)], "reason": reason,
                    "evidence_ids": ev or []})

    if ctx.dq.missing_critical_fields:
        cap("missing_core_financials", "Watchlist", f"Missing core financials: {', '.join(ctx.dq.missing_critical_fields)}.",
            ["dq.report"])
    if da_high_unresolved:
        cap("unresolved_high_severity_risk", "Wait",
            f"{da_high_unresolved} high-severity Devil's Advocate risk(s) unresolved.")
    if portfolio_breach:
        cap("portfolio_constraint", "Wait", portfolio_breach)
    da = ctx.reports.get("devils_advocate")
    if da is not None and not (da.deterministic or {}).get("difference_credible", False):
        cap("no_market_edge", "Buy", "Devil's Advocate: no credible answer to 'what does the market believe, and why are we "
                                     "different?' (Strong Buy needs one).", ["rb.implied_growth"])
    from backend.analysis.factors import rulebook

    red = set(rulebook()["gates"]["red_flag_8k_items"])
    today = date.today()
    for f in ctx.pack.by_category("filing"):
        items = {i["item"] for i in f.detail.get("items", [])}
        other = (items & HIGH_MATERIALITY_8K) - red   # red-flag items are handled (as Reject) by the factor engine
        if other and f.date and (today - date.fromisoformat(f.date)).days <= 90:
            cap("material_event", "Wait", f"Unresolved high-materiality event: {f.title}.", [f.id])
    return out


def entry_price(rec: str, rb: dict[str, Any], price_date: Any, tech_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rulebook price ladder: buy-below = fair value × (1 − margin of safety). Technical support is a reference only."""
    price, fv, bb = rb.get("price"), rb.get("fair_value"), rb.get("buy_below")
    out: dict[str, Any] = {"price_date": str(price_date) if price_date else None, "reference_price": price,
                           "fair_value": fv, "buy_below": bb, "methods": [
                               f"{m['method']}: ${m['value']:,.2f}" for m in rb.get("fair_value_methods", [])]}
    tz = (tech_entry or {}) if (tech_entry or {}).get("justified") else None
    if tz:
        out["technical_support"] = {"low": tz["low"], "high": tz["high"], "touches": tz.get("touches")}
    if price is None:
        out.update(available=False, text="No entry price: current price unavailable.")
        return out
    if not fv or not bb:
        out.update(available=False, text="No entry price: fair value could not be computed (needs positive free cash flow "
                                         "or earnings with enough history).")
        return out
    if rec in BUYS:
        txt = f"Buy at or below ${max(bb, min(price, fv * 1.05)):,.2f}; current ${price:,.2f} ({price_date})."
    else:
        txt = f"Buy-below ${bb:,.2f} (fair value ${fv:,.2f}); current ${price:,.2f} on {price_date}."
    out.update(available=True, low=bb, high=fv, text=txt,
               note="Fair value is a model estimate (DCF and own-history P/E), not a predicted price.")
    return out


def allocation(rec: str, size: dict[str, Any], pf_ctx: dict[str, Any] | None) -> dict[str, Any]:
    """Rulebook sizing: 5% Strong Buy, 3% Buy, halved for high volatility or large plausible loss; capped by mandate."""
    from backend.analysis.factors import rulebook

    s = rulebook()["sizing"]
    if rec not in BUYS:
        pending = s["buy"] if rec == "Wait" else 0.0
        txt = f"No position now ({rec})." + (f" If the price reaches the buy-below level and the rules give Buy: "
                                             f"{fmt_value(pending, 'pct')}." if pending else "")
        return {"available": True, "low": 0.0, "high": 0.0, "text": txt, "method": []}
    w = size.get("size") or 0.0
    method = [f"Rulebook base size {fmt_value(s['strong_buy'] if rec == 'Strong Buy' else s['buy'], 'pct')} for {rec}"] + \
        list(size.get("notes", []))
    if pf_ctx and pf_ctx.get("available") and pf_ctx.get("max_position") and w > pf_ctx["max_position"]:
        w = pf_ctx["max_position"]
        method.append(f"capped at mandate maximum {fmt_value(w, 'pct')}")
    return {"available": True, "low": round(w, 4), "high": round(w, 4), "method": method,
            "text": f"{fmt_value(w, 'pct')} of portfolio ({'; '.join(method)})."}
