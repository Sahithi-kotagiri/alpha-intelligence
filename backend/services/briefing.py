"""Spoken/written briefings generated from the saved structured report (never improvised)."""

from __future__ import annotations

from typing import Any

from backend.tools.types import fmt_value


def build_briefing(result: dict[str, Any]) -> dict[str, Any]:
    f = result["final"]
    ident = result["identity"]
    sc = f["scores"]
    da = result["reports"].get("devils_advocate", {})
    objections = (da.get("deterministic") or {}).get("llm_objections") or (da.get("deterministic") or {}).get("objections") or []
    strongest = sorted(objections, key=lambda o: {"high": 0, "medium": 1, "low": 2}[o["severity"]])[:1]
    reasons = [c["text"] for c in f.get("thesis", [])][:3]
    risks = [r["text"] for r in f.get("risks", [])][:2]
    pf = result.get("portfolio") or {}
    dq = result["data_quality"]
    sections = [
        {"title": "Recommendation", "text": f"{ident['name']}: {f['recommendation']}, with {round(f['confidence'] * 100)} percent "
                                            "confidence in the quality of the assessment. This is not a probability of profit."},
        {"title": "Main reasons", "text": " ".join(reasons) if reasons else "No rule-based supporting reasons were identified."},
        {"title": "Risks and the strongest objection",
         "text": " ".join(risks) + (f" The Devil's Advocate's strongest objection: {strongest[0]['objection']}" if strongest else "")},
        {"title": "Valuation and timing",
         "text": f"Valuation scores {sc.get('valuation')} out of 10 against peers, and the technical setup scores "
                 f"{sc.get('technical')} out of 10. Entry price: {f['entry_price'].get('text')}"},
        {"title": "Portfolio implications",
         "text": (f"Adding the stock at an illustrative {fmt_value((pf.get('impact') or {}).get('candidate_weight'), 'pct')} would change "
                  f"portfolio volatility from {fmt_value((pf.get('impact') or {}).get('vol_before'), 'pct')} to "
                  f"{fmt_value((pf.get('impact') or {}).get('vol_after'), 'pct')}. {f['allocation'].get('text')}")
         if pf.get("available") else "No portfolio was supplied, so portfolio implications were not assessed."},
        {"title": "What would change the thesis",
         "text": " ".join((f["monitoring_plan"].get("invalidation_conditions") or [])[:3]) or "No invalidation conditions recorded."},
        {"title": "Data limitations",
         "text": f"Data quality is {dq['status']}. Prices are through {f['meta']['price_date']} and financials through "
                 f"{f['meta']['financials_through']}. " + (f"Missing critical data: {', '.join(dq['missing_critical_fields'])}. "
                                                            if dq.get("missing_critical_fields") else "")
                 + ("Agents ran in rule-based mode without an LLM." if result.get("mode") != "llm" else "")},
    ]
    text = "\n\n".join(f"{s['title']}. {s['text']}" for s in sections)
    return {"analysis_id": f["meta"]["analysis_id"], "sections": sections, "text": text,
            "source": "Generated from the saved structured report; no new analysis is performed."}


def agent_summary(result: dict[str, Any], agent: str) -> dict[str, Any]:
    r = result["reports"].get(agent)
    if r is None:
        raise KeyError(agent)
    s = (r.get("score") or {}).get("score")
    parts = [f"{r['title']}. {r['summary']}"]
    if s is not None:
        parts.append(f"Score {s} out of 10, confidence {r['confidence']}.")
    if r.get("strengths"):
        parts.append("Strengths: " + " ".join(c["text"] for c in r["strengths"][:3]))
    if r.get("weaknesses"):
        parts.append("Weaknesses: " + " ".join(c["text"] for c in r["weaknesses"][:3]))
    return {"agent": agent, "text": " ".join(parts), "source": "Generated from the saved agent report."}
