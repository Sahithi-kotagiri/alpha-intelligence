"""Committee rules: agreement/disagreement detection, decision gates, policy, confidence,
entry price, allocation and monitoring plan. All thresholds come from decision_policy.yaml.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from backend.analysis.config_loader import policy
from backend.tools.types import fmt_value

RECS = ["Buy Now", "Wait", "Watchlist", "Reject"]
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
            "rather than Buy Now unless growth-adjusted valuation is acceptable).",
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
            [], "High-severity objections must be addressed before Buy Now.", "fundamental",
            "Which high-severity objection is least supported by the evidence?")
    return agreements, dis, qs


def gates(ctx, scores: dict[str, float | None], da_high_unresolved: int, portfolio_breach: str | None,
          confidence: float) -> list[dict[str, Any]]:
    p = policy()
    out = []
    if ctx.dq.blocks_buy_now:
        out.append({"id": "data_quality_critical", "blocks": ["Buy Now"], "reason": f"Data quality is {ctx.dq.status} or a material conflict exists."})
    if ctx.dq.missing_critical_fields:
        out.append({"id": "missing_critical_financials", "blocks": ["Buy Now"],
                    "reason": f"Missing critical fields: {', '.join(ctx.dq.missing_critical_fields)}."})
    if da_high_unresolved:
        out.append({"id": "unresolved_high_severity_objection", "blocks": ["Buy Now"],
                    "reason": f"{da_high_unresolved} high-severity Devil's Advocate objection(s) unresolved."})
    v = scores.get("valuation")
    if v is not None and v < 2.5:
        out.append({"id": "extreme_valuation_unexplained", "blocks": ["Buy Now"], "reason": f"Valuation score {v:.1f}/10 is extreme."})
    if portfolio_breach:
        out.append({"id": "portfolio_constraint", "blocks": ["Buy Now"], "reason": portfolio_breach})
    today = date.today()
    for f in ctx.pack.by_category("filing"):
        items = {i["item"] for i in f.detail.get("items", [])}
        if items & HIGH_MATERIALITY_8K and f.date and (today - date.fromisoformat(f.date)).days <= 90:
            out.append({"id": "thesis_invalidating_event", "blocks": ["Buy Now"],
                        "reason": f"Unresolved high-materiality event: {f.title}.", "evidence_ids": [f.id]})
    if confidence < p["thresholds"]["min_confidence_for_buy"]:
        out.append({"id": "low_confidence", "blocks": ["Buy Now"],
                    "reason": f"Confidence {confidence:.0%} below {p['thresholds']['min_confidence_for_buy']:.0%} minimum for Buy Now."})
    return out


def policy_recommendation(scores: dict[str, float | None], blocked: set[str]) -> tuple[str, list[str]]:
    th = policy()["thresholds"]
    q, v, t, r = (scores.get(k) for k in ("quality", "valuation", "technical", "risk"))
    trace = []
    rj = th["reject"]
    if q is not None and (q <= rj["quality_max"] or (r is not None and r <= rj["risk_max_with_weak_quality"] and q <= rj["weak_quality_max"])):
        trace.append(f"Reject rule: quality {q:.1f} ≤ {rj['quality_max']} or (risk {r} ≤ {rj['risk_max_with_weak_quality']} "
                     f"and quality ≤ {rj['weak_quality_max']}).")
        return "Reject", trace
    b = th["buy_now"]
    meets_buy = all(x is not None for x in (q, v, t, r)) and q >= b["quality_min"] and v >= b["valuation_min"] and \
        t >= b["technical_min"] and r >= b["risk_min"]
    if meets_buy and "Buy Now" not in blocked:
        trace.append(f"Buy Now rule met: quality ≥{b['quality_min']}, valuation ≥{b['valuation_min']}, technical ≥{b['technical_min']}, "
                     f"risk ≥{b['risk_min']}, and no gate blocks it.")
        return "Buy Now", trace
    if meets_buy:
        trace.append("Buy Now thresholds met but blocked by a decision gate.")
    else:
        fails = [f"{n} {x if x is None else round(x, 1)} < {m}" for n, x, m in
                 (("quality", q, b["quality_min"]), ("valuation", v, b["valuation_min"]), ("technical", t, b["technical_min"]),
                  ("risk", r, b["risk_min"])) if x is None or x < m]
        trace.append("Buy Now thresholds not met: " + "; ".join(fails) + ".")
    if q is not None and q >= th["wait"]["quality_min"]:
        trace.append(f"Wait rule: business quality {q:.1f} ≥ {th['wait']['quality_min']} but entry conditions not met.")
        return "Wait", trace
    trace.append("Watchlist: neither Buy Now, Wait nor Reject conditions met.")
    return "Watchlist", trace


def confidence(ctx, unavailable_dims: int, n_disagreements: int, n_unresolved: int, llm_used: bool, news_scored: bool) -> tuple[float, list[str]]:
    c = policy()["confidence"]
    p = c["penalties"]
    val = c["base"]
    why = [f"base {c['base']:.0%}"]
    val -= ctx.dq.confidence_penalty
    why.append(f"data quality {ctx.dq.status} −{ctx.dq.confidence_penalty:.0%}")
    if unavailable_dims:
        val -= p["per_unavailable_dimension"] * unavailable_dims
        why.append(f"{unavailable_dims} unavailable score dimension(s) −{p['per_unavailable_dimension'] * unavailable_dims:.0%}")
    if n_disagreements:
        val -= p["per_material_disagreement"] * n_disagreements
        why.append(f"{n_disagreements} material disagreement(s) −{p['per_material_disagreement'] * n_disagreements:.0%}")
    if n_unresolved:
        val -= p["per_unresolved_question"] * n_unresolved
        why.append(f"{n_unresolved} unresolved question(s) −{p['per_unresolved_question'] * n_unresolved:.0%}")
    if not llm_used:
        val -= p["no_llm_narrative"]
        why.append(f"rule-based only (no LLM interpretation of qualitative evidence) −{p['no_llm_narrative']:.0%}")
    if not news_scored:
        val -= p["news_not_assessed"]
        why.append(f"news not assessed −{p['news_not_assessed']:.0%}")
    val = max(c["floor"], min(c["cap"], val))
    return round(val, 3), why


def entry_price(rec: str, price: float | None, price_date: Any, tech_entry: dict[str, Any], assessments: list,
                eps_ttm: float | None, peer_pe_median: float | None, fwd_eps: float | None,
                peer_fwd_pe_median: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {"price_date": str(price_date) if price_date else None, "reference_price": price, "methods": []}
    if price is None:
        out.update(available=False, text="No reliable entry price can be established: price unavailable.")
        return out
    fv = []
    if eps_ttm and eps_ttm > 0 and peer_pe_median:
        fv.append(eps_ttm * peer_pe_median)
        out["methods"].append(f"TTM diluted EPS ${eps_ttm:.2f} × peer median trailing P/E {peer_pe_median:.1f} = ${eps_ttm * peer_pe_median:,.2f}")
    if fwd_eps and fwd_eps > 0 and peer_fwd_pe_median:
        fv.append(fwd_eps * peer_fwd_pe_median)
        out["methods"].append(f"Consensus next-FY EPS ${fwd_eps:.2f} (estimate) × peer median forward P/E {peer_fwd_pe_median:.1f} "
                              f"= ${fwd_eps * peer_fwd_pe_median:,.2f}")
    tz = tech_entry if tech_entry.get("justified") else None
    if tz:
        out["methods"].append(f"Technical support zone ${tz['low']:,.2f}–${tz['high']:,.2f} ({tz['touches']} touches)")
    lo = hi = None
    if tz and fv:
        if max(fv) < tz["low"] * 0.95 or min(fv) > tz["high"] * 1.05:
            out.update(available=False, divergent=True,
                       text=f"No reliable entry range: technical support (${tz['low']:,.2f}–${tz['high']:,.2f}) and peer-relative "
                            f"valuation anchors (${min(fv):,.2f}–${max(fv):,.2f}) disagree materially. Treat both as reference "
                            "levels, not entry prices.")
            return out
        lo, hi = max(tz["low"], min(fv)), min(tz["high"], max(fv))
        if lo > hi:
            lo, hi = tz["low"], tz["high"]
    elif tz:
        lo, hi = tz["low"], tz["high"]
    elif fv:
        v = sorted(fv)
        lo, hi = v[0], v[-1] if len(v) > 1 else v[0] * 1.05
    if lo is None:
        out.update(available=False, text="No reliable entry price can be established from available evidence "
                                         "(no justified support zone and no peer-relative valuation anchor).")
        return out
    lo, hi = round(min(lo, hi), 2), round(max(lo, hi), 2)
    if rec == "Buy Now":
        hi = max(hi, round(price, 2))
    out.update(available=True, low=lo, high=hi, text=f"${lo:,.2f}–${hi:,.2f} (price ${price:,.2f} on {price_date}).",
               note="Estimates derived from the methods listed; not a guaranteed or predicted price.")
    return out


def allocation(rec: str, conf: float, pf_ctx: dict[str, Any] | None, stock_vol: float | None) -> dict[str, Any]:
    p = policy()["allocation"]
    if not pf_ctx or not pf_ctx.get("available"):
        return {"available": False, "text": "Allocation cannot responsibly be determined: supply portfolio holdings, total value "
                                            "or weights, risk tolerance and investment horizon.",
                "required_inputs": ["holdings", "risk_tolerance", "horizon"]}
    tol = pf_ctx.get("risk_tolerance")
    if tol not in p["max_position_by_tolerance"]:
        return {"available": False, "text": "Allocation needs a stated risk tolerance (conservative / moderate / aggressive).",
                "required_inputs": ["risk_tolerance"]}
    if rec in ("Reject", "Watchlist"):
        return {"available": True, "low": 0.0, "high": 0.0, "text": f"No allocation: recommendation is {rec}."}
    cap = p["max_position_by_tolerance"][tol]
    if pf_ctx.get("max_position"):
        cap = min(cap, pf_ctx["max_position"])
    w = cap
    method = [f"Risk-tolerance cap {cap:.0%} ({tol})"]
    pvol = (pf_ctx.get("impact") or {}).get("vol_before")
    corr = (pf_ctx.get("impact") or {}).get("candidate_corr_to_portfolio")
    if stock_vol and pvol and corr and corr > 0:
        target = p["target_vol_contribution_by_tolerance"][tol]
        w_vol = target * pvol / (stock_vol * corr)
        method.append(f"risk-budget size {w_vol:.1%} = target contribution {target:.0%} × portfolio vol {pvol:.1%} ÷ "
                      f"(stock vol {stock_vol:.1%} × correlation {corr:.2f})")
        w = min(w, w_vol)
    w = w * min(1.0, conf / 0.9)
    method.append(f"scaled by confidence {conf:.0%}/90%")
    lo, hi = round(w * 0.5, 4), round(w, 4)
    prefix = "If entry conditions are met: " if rec == "Wait" else ""
    return {"available": True, "low": lo, "high": hi, "method": method,
            "text": f"{prefix}{fmt_value(lo, 'pct')}–{fmt_value(hi, 'pct')} of portfolio (risk-budget sizing, illustrative)."}
