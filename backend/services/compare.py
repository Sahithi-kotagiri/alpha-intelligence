"""Compare two saved analysis versions: what changed and why the recommendation moved."""

from __future__ import annotations

from typing import Any

KEY_METRICS = ["revenue_yoy_quarterly", "revenue_cagr_3y", "diluted_eps_yoy_quarterly", "operating_margin_ttm", "fcf_margin_ttm",
               "roic_annual", "debt_to_equity", "net_debt_to_ebitda", "trailing_pe", "forward_pe", "peg_forward", "ev_to_ebitda"]


def compare(a: dict[str, Any], b: dict[str, Any], a_id: str, b_id: str) -> dict[str, Any]:
    fa, fb = a["final"], b["final"]
    sa, sb = fa["scores"], fb["scores"]
    scores = [{"score": k, "a": sa.get(k), "b": sb.get(k),
               "change": (sb[k] - sa[k]) if isinstance(sa.get(k), (int, float)) and isinstance(sb.get(k), (int, float)) else None}
              for k in ("business_quality", "valuation", "technical", "news", "risk")]
    am = {x["metric_key"]: x for x in a.get("assessments", [])}
    bm = {x["metric_key"]: x for x in b.get("assessments", [])}
    metrics = []
    for k in KEY_METRICS:
        x, y = am.get(k), bm.get(k)
        if not x and not y:
            continue
        metrics.append({"metric": k, "label": (y or x)["label"], "a": x and x["display"], "b": y and y["display"],
                        "a_verdict": x and x["verdict"], "b_verdict": y and y["verdict"],
                        "verdict_changed": bool(x and y and x["verdict"] != y["verdict"])})
    ta = {t["key"]: t for t in a.get("thesis", [])}
    tb = {t["key"]: t for t in b.get("thesis", [])}
    thesis = [{"key": k, "a": ta.get(k, {}).get("description"), "b": tb.get(k, {}).get("description")} for k in sorted(set(ta) | set(tb))]
    ga = {g["id"] for g in fa.get("gates", [])}
    gb = {g["id"] for g in fb.get("gates", [])}
    drivers = []
    if fa["recommendation"] != fb["recommendation"]:
        drivers += [f"Gate added: {g}" for g in gb - ga] + [f"Gate removed: {g}" for g in ga - gb]
        drivers += [f"{s['score']} score {s['a']} → {s['b']}" for s in scores if s["change"] and abs(s["change"]) >= 1]
        drivers += [f"{m['label']}: {m['a_verdict']} → {m['b_verdict']}" for m in metrics if m["verdict_changed"]]
    return {"a": {"id": a_id, "recommendation": fa["recommendation"], "confidence": fa["confidence"],
                  "price_date": fa["meta"]["price_date"], "financials_through": fa["meta"]["financials_through"]},
            "b": {"id": b_id, "recommendation": fb["recommendation"], "confidence": fb["confidence"],
                  "price_date": fb["meta"]["price_date"], "financials_through": fb["meta"]["financials_through"]},
            "recommendation_changed": fa["recommendation"] != fb["recommendation"], "scores": scores, "metrics": metrics,
            "thesis": thesis, "gates": {"a": sorted(ga), "b": sorted(gb)},
            "drivers": drivers or (["Recommendation unchanged."] if fa["recommendation"] == fb["recommendation"] else
                                   ["No single rule-level driver identified; see committee rationale."]),
            "decision_trace": {"a": fa.get("decision_trace"), "b": fb.get("decision_trace")}}
