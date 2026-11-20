"""Follow-up research assistant grounded in a saved analysis.

With an LLM: answers from the saved report + a relevance-ranked subset of the evidence pack,
citations validated against the pack. Without an LLM: answers common question types from
the structured report by rule (labelled), and says plainly when a question needs an LLM
or fresh data. Saved data is never presented as current.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from backend.agents.base import load_prompt
from backend.agents.guardrails import strip_invalid
from backend.agents.llm import LLMClient, LLMError
from backend.analysis.evidence import EvidenceItem, render_for_prompt
from backend.schemas.agents import AssistantAnswer

FRESH_WORDS = ("today", "now", "latest news", "this week", "current price", "right now", "currently trading")


def _rank(question: str, items: list[EvidenceItem], k: int = 60) -> list[EvidenceItem]:
    words = {w for w in re.findall(r"[a-z]{3,}", question.lower())}
    scored = []
    for i in items:
        hay = f"{i.title} {i.display} {i.category} {(i.detail or {}).get('explanation', '')}".lower()
        s = sum(1 for w in words if w in hay) + (0.5 if i.category in ("assessment", "data_quality") else 0)
        scored.append((s, i))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored[:k]]


def needs_fresh(question: str, result: dict[str, Any]) -> bool:
    pdate = result["final"]["meta"].get("price_date")
    stale = pdate is None or (date.today() - date.fromisoformat(pdate)).days > 1
    return stale and any(w in question.lower() for w in FRESH_WORDS)


def rule_answer(question: str, result: dict[str, Any], compare_prev: dict[str, Any] | None) -> dict[str, Any]:
    q = question.lower()
    f = result["final"]
    c = result["committee"]
    da = (result["reports"].get("devils_advocate") or {}).get("deterministic") or {}
    objs = da.get("llm_objections") or da.get("objections") or []

    def out(text, cites, section):
        return {"answer": text, "citations": cites, "section": section, "mode": "rule-based",
                "needs_fresh_data": needs_fresh(question, result),
                "uncertainty": "Answered by rule from the saved report; no LLM interpretation."}

    if ("why" in q and any(r.lower() in q for r in ("wait", "buy", "watchlist", "reject"))) or "why did" in q:
        return out(f"The committee chose {f['recommendation']}. {' '.join(f['decision_trace'])} Alternatives: "
                   + " ".join(c.get("why_not_alternatives", [])), [g.get("id") for g in f.get("gates", [])], "committee")
    if "not to buy" in q or "strongest reason" in q or "bear" in q:
        if not objs:
            return out("No rule-based objection was triggered in this analysis.", [], "devils_advocate")
        o = sorted(objs, key=lambda o: {"high": 0, "medium": 1, "low": 2}[o["severity"]])[0]
        return out(f"{o['objection']} Mechanism: {o['mechanism']} What would weaken it: {o['what_would_weaken_it']}",
                   o["evidence_ids"], "devils_advocate")
    if "deteriorat" in q or "worsen" in q:
        worst = None
        for m in result.get("metrics", []):
            if m["key"].endswith("_yoy_quarterly") and m.get("value") is not None and m["status"] == "ok":
                if worst is None or m["value"] < worst["value"]:
                    worst = m
        unf = [a for a in result["assessments"] if a["verdict"] == "unfavourable"]
        txt = (f"Weakest year-over-year change: {worst['label']} {worst['value']:.1%} ({worst['period']}). " if worst else "")
        txt += "Metrics rated unfavourable: " + ", ".join(f"{a['label']} {a['display']}" for a in unf[:6]) + "." if unf else ""
        return out(txt or "No deteriorating metric identified.", ([worst["id"]] if worst else []) + [f"asm.{a['metric_key']}" for a in unf[:6]], "metrics")
    if "changed" in q and ("since" in q or "last" in q):
        if not compare_prev:
            return out("There is no earlier completed analysis of this company to compare with.", [], "history")
        return out(f"Previous: {compare_prev['a']['recommendation']} ({compare_prev['a']['price_date']}); now: "
                   f"{compare_prev['b']['recommendation']} ({compare_prev['b']['price_date']}). Drivers: "
                   + "; ".join(compare_prev["drivers"]), [], "history")
    if "assumption" in q or "at risk" in q or "thesis" in q:
        th = result.get("thesis", [])
        return out("Thesis assumptions at decision time: " + "; ".join(t["description"] for t in th)
                   + ". Current statuses are on the Thesis Monitor page.", [e for t in th for e in t.get("evidence_ids", []) if e], "thesis")
    if "change" in q and "recommend" in q or "what would need" in q:
        return out("Decision rules: " + " ".join(f["decision_trace"]) + " Gates in force: "
                   + ("; ".join(g["reason"] for g in f["gates"]) or "none") + ". Invalidation conditions: "
                   + "; ".join(f["monitoring_plan"].get("invalidation_conditions") or []), [], "committee")
    if "portfolio" in q:
        pf = result.get("portfolio") or {}
        if not pf.get("available"):
            return out("No portfolio was supplied with this analysis, so its portfolio effect was not assessed.", [], "portfolio")
        imp = pf["impact"]
        return out(f"At an illustrative {imp['candidate_weight']:.0%} weight, portfolio volatility moves from "
                   f"{imp.get('vol_before') or 0:.1%} to {imp.get('vol_after') or 0:.1%} and correlation with the portfolio is "
                   f"{imp.get('candidate_corr_to_portfolio')}. {f['allocation']['text']}", ["pf.vol_before", "pf.vol_after"], "portfolio")
    if "valuation" in q or "expensive" in q or "cheap" in q:
        keys = ("trailing_pe", "forward_pe", "peg_forward", "ev_to_ebitda", "pe_vs_own_history")
        av = [a for a in result["assessments"] if a["metric_key"] in keys]
        return out(" ".join(a["explanation"] for a in av), [f"asm.{a['metric_key']}" for a in av], "valuation")
    if "earnings" in q:
        rows = [m for m in result.get("metrics", []) if m["key"] in ("revenue_yoy_quarterly", "diluted_eps_yoy_quarterly",
                                                                      "operating_margin_quarterly") and m["status"] == "ok"]
        rows = sorted(rows, key=lambda m: m.get("period_end") or "")[-3:]
        return out("Latest quarter: " + "; ".join(f"{m['label']} {m['value']:.1%} ({m['period']})" for m in rows),
                   [m["id"] for m in rows], "fundamental")
    return {"answer": "This question needs open-ended interpretation, which requires an LLM provider (not configured). "
                      "Try asking about the recommendation, the strongest reason not to buy, deteriorating metrics, "
                      "what changed, thesis assumptions, valuation, earnings or portfolio impact.",
            "citations": [], "section": None, "mode": "unsupported", "needs_fresh_data": needs_fresh(question, result),
            "uncertainty": "Not answered."}


def answer(question: str, result: dict[str, Any], items: list[EvidenceItem], llm: LLMClient | None,
           compare_prev: dict[str, Any] | None, history: list[dict[str, str]]) -> dict[str, Any]:
    if llm is None:
        return rule_answer(question, result, compare_prev)
    f = result["final"]
    ranked = _rank(question, items)
    ctx = (f"Saved analysis {f['meta']['analysis_id']} of {result['identity']['name']} ({result['identity']['ticker']}), "
           f"generated {f['meta']['generated_at']}; prices through {f['meta']['price_date']}; financials through "
           f"{f['meta']['financials_through']}. Today is {datetime.now(UTC).date()}.\nRecommendation {f['recommendation']} "
           f"({f['confidence']:.0%}). Executive summary: {f['executive_summary']}\nDecision trace: {f['decision_trace']}\n"
           f"Committee rationale: {result['committee'].get('rationale')}\nWhy not alternatives: {result['committee'].get('why_not_alternatives')}\n"
           f"Agent summaries: {[(k, r['summary']) for k, r in result['reports'].items()]}\nThesis assumptions: "
           f"{[t['description'] for t in result.get('thesis', [])]}\nComparison with previous version: {compare_prev}\n"
           f"Conversation so far: {history[-6:]}\n\nEvidence:\n{render_for_prompt(ranked)}")
    try:
        res = llm.structured(load_prompt("common")[0] + "\n\n" + load_prompt("assistant")[0],
                             f"{ctx}\n\nQuestion: {question}", AssistantAnswer, 4000)
        data, _ = strip_invalid(res.parsed, {i.id for i in items})
        data["mode"] = "llm"
        data["needs_fresh_data"] = data["needs_fresh_data"] or needs_fresh(question, result)
        data["model"] = res.model
        return data
    except LLMError as exc:
        r = rule_answer(question, result, compare_prev)
        r["uncertainty"] += f" (LLM failed: {exc})"
        return r
