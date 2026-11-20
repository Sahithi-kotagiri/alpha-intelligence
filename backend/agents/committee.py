"""Investment Committee: structured review, gates, bounded review loop, policy-constrained decision.

The committee never averages scores. Deterministic rules detect disagreements and apply
gates; the policy proposes one recommendation from the allowed set. With an LLM, the
committee may choose another *allowed* recommendation, but only with an evidence-cited
override reason that is stored alongside the policy recommendation.
"""

from __future__ import annotations

import json
from typing import Any

from backend.agents.base import AgentContext, load_prompt
from backend.agents.guardrails import invalid_citations, strip_invalid
from backend.agents.llm import LLMClient, LLMError
from backend.analysis import decision as D
from backend.analysis.config_loader import policy
from backend.analysis.evidence import render_for_prompt
from backend.schemas.agents import CommitteeNarrative


def scores_from(ctx: AgentContext) -> dict[str, float | None]:
    s = ctx.scores
    news = ctx.reports.get("news")
    return {"quality": s["fundamental_quality"].score, "valuation": s["valuation"].score,
            "technical": s["technical"].score if s.get("technical") else None,
            "risk": s["risk"].score if s.get("risk") else None,
            "news": (news.score or {}).get("score") if news and news.score else None}


def portfolio_breach(ctx: AgentContext) -> str | None:
    pf = ctx.portfolio or {}
    if not pf.get("available"):
        return None
    sector = ctx.profile.get("sector")
    if sector and sector in (pf.get("excluded_sectors") or []):
        return f"Sector '{sector}' is excluded by the portfolio mandate."
    if pf.get("max_position") and (pf.get("impact") or {}).get("candidate_weight", 0) > pf["max_position"]:
        return "Illustrative position exceeds the stated maximum position size."
    return None


class InvestmentCommittee:
    name = "committee"
    title = "Investment Committee"

    def deliberate(self, ctx: AgentContext, llm: LLMClient | None, emit, round_no: int,
                   review_log: list[dict[str, Any]]) -> dict[str, Any]:
        emit("committee", "started", f"Committee review round {round_no + 1}", agent="committee")
        scores = scores_from(ctx)
        da = ctx.reports.get("devils_advocate")
        da_high = int((da.deterministic or {}).get("high_severity_count", 0)) if da else 0
        agreements, dis, det_qs = D.detect(ctx, scores, da_high)
        resolved_topics = {r.get("topic") for r in review_log if r.get("response", {}).get("resolved")}
        for d in dis:
            if d["topic"] in resolved_topics:
                d["resolved"] = True
                ans = next(r for r in review_log if r.get("topic") == d["topic"])
                d["resolution"] += f" Review answer: {ans['response'].get('answer')}"
        da_unresolved = da_high - (1 if "Bear case contests bullish analyst views" in resolved_topics else 0)
        unresolved = [d for d in dis if not d["resolved"]]
        unavailable_dims = sum(len(ctx.scores[k].unavailable_dimensions) for k in ("fundamental_quality", "valuation")
                               if ctx.scores.get(k)) + (0 if ctx.scores.get("technical") else 1)
        news_scored = scores["news"] is not None
        llm_used = any(r.mode == "llm" for r in ctx.reports.values())
        conf, conf_why = D.confidence(ctx, unavailable_dims, len(dis), len(unresolved), llm_used, news_scored)
        g = D.gates(ctx, scores, max(da_unresolved, 0), portfolio_breach(ctx), conf)
        blocked = {b for x in g for b in x["blocks"]}
        allowed = [r for r in D.RECS if r not in blocked]
        policy_rec, trace = D.policy_recommendation(scores, blocked)
        out: dict[str, Any] = {
            "round": round_no + 1, "scores": scores, "agreements": agreements, "disagreements": dis,
            "consistency_checks": D.consistency_checks(ctx), "gates": g, "allowed": allowed,
            "policy_recommendation": policy_rec, "policy_trace": trace, "recommendation": policy_rec,
            "overridden": False, "override_reason": "", "confidence": conf, "confidence_explanation": conf_why,
            "review_questions": [], "unresolved_questions": [d["description"] for d in unresolved],
            "mode": "deterministic", "why_not_alternatives": self._why_not(policy_rec, scores, g),
            "rationale": " ".join(trace), "evidence_adjudication": [], "limitations": [],
        }
        max_rounds = policy()["review_loop"]["max_rounds"]
        if llm is None:
            if round_no == 0 and det_qs:
                out["review_questions"] = det_qs[: policy()["review_loop"]["max_questions_per_round"]]
            emit("committee", "completed", f"Committee round {round_no + 1}: policy recommendation {policy_rec} "
                 f"({len(dis)} disagreement(s), {len(g)} gate(s))", agent="committee")
            return out
        # ---- LLM committee
        allowed_ids = ctx.pack.ids()
        reports = "\n\n".join(
            f"## {r.title} [{r.agent}] mode={r.mode} score={r.score.get('score') if r.score else 'n/a'} confidence={r.confidence}\n"
            f"{r.summary}\nStrengths: {[c.model_dump() for c in r.strengths][:5]}\nWeaknesses: {[c.model_dump() for c in r.weaknesses][:8]}"
            for r in ctx.reports.values())
        key_ev = [i for i in ctx.pack.items.values() if i.category in ("assessment", "data_quality", "scenario", "calendar", "portfolio")
                  or i.id.startswith(("tech.trend", "risk.", "sec.8-K"))]
        user = (f"Company: {ctx.company_name} ({ctx.ticker}); horizon {ctx.params.get('horizon')}; benchmark {ctx.params.get('benchmark')}.\n"
                f"Data through: {json.dumps(ctx.data_through, default=str)}\n\nAnalyst reports:\n{reports}\n\n"
                f"Rule-detected disagreements: {json.dumps(dis)}\nDecision gates: {json.dumps(g)}\n"
                f"ALLOWED recommendations: {allowed}\nPolicy recommendation: {policy_rec} — trace: {trace}\n"
                f"Confidence (rule-based): {conf:.0%} — {conf_why}\nPrevious review rounds: {json.dumps(review_log, default=str)}\n"
                f"Review rounds remaining after this one: {max(0, max_rounds - round_no - 1)}\n\n"
                f"Key evidence:\n{render_for_prompt(key_ev)}")
        system = load_prompt("common")[0] + "\n\n" + load_prompt("committee")[0]
        try:
            emit("committee", "analysing", "Committee LLM adjudication", agent="committee")
            res = llm.structured(system, user, CommitteeNarrative)
            bad = invalid_citations(res.parsed, allowed_ids)
            data, _ = strip_invalid(res.parsed, allowed_ids)
            v, sha = load_prompt("committee")[1], load_prompt("committee")[2]
            ctx.model_runs.append({"agent": "committee", "provider": res.provider, "model": res.model, "prompt_name": "committee",
                                   "prompt_version": v, "prompt_sha256": sha, "input_tokens": res.input_tokens,
                                   "output_tokens": res.output_tokens, "latency_ms": res.latency_ms, "status": "ok",
                                   "stop_reason": res.stop_reason, "guardrail_violations": bad})
            chosen = data["chosen_recommendation"]
            if chosen not in allowed:
                out["limitations"].append(f"LLM chose {chosen}, which is blocked by a gate; policy recommendation kept.")
                chosen = policy_rec
            out.update(mode="llm", agreements=data["agreements"] or agreements,
                       disagreements=data["disagreements"] + [d for d in dis if d["topic"] not in {x["topic"] for x in data["disagreements"]}],
                       evidence_adjudication=data["evidence_adjudication"], rationale=data["rationale"],
                       why_not_alternatives=data["why_not_alternatives"] or out["why_not_alternatives"],
                       unresolved_questions=data["unresolved_questions"], limitations=out["limitations"] + data["limitations"],
                       recommendation=chosen)
            if chosen != policy_rec:
                if not data["override_reason"].strip():
                    out["recommendation"] = policy_rec
                    out["limitations"].append("LLM override discarded: no override reason given.")
                else:
                    out["overridden"] = True
                    out["override_reason"] = data["override_reason"]
            if round_no + 1 < max_rounds:
                out["review_questions"] = [q | {"topic": q["question"][:80]} for q in data["review_questions"]][
                    : policy()["review_loop"]["max_questions_per_round"]]
            out["llm_invalid_citations"] = bad
        except LLMError as exc:
            out["limitations"].append(f"Committee LLM step failed ({exc}); policy recommendation used.")
            out["mode"] = "deterministic_fallback"
            if round_no == 0:
                out["review_questions"] = det_qs[: policy()["review_loop"]["max_questions_per_round"]]
        emit("committee", "completed", f"Committee round {round_no + 1}: {out['recommendation']} "
             f"(policy {policy_rec}{', overridden' if out['overridden'] else ''})", agent="committee")
        return out

    @staticmethod
    def _why_not(rec: str, scores: dict[str, float | None], g: list[dict[str, Any]]) -> list[str]:
        q, v, t, r = (scores.get(k) for k in ("quality", "valuation", "technical", "risk"))
        th = policy()["thresholds"]
        out = []
        blocked_buy = [x["reason"] for x in g if "Buy Now" in x["blocks"]]
        if rec != "Buy Now":
            out.append("Buy Now: " + ("; ".join(blocked_buy) if blocked_buy else
                                      f"thresholds not met (quality {q}, valuation {v}, technical {t}, risk {r} vs minimums "
                                      f"{th['buy_now']})."))
        if rec != "Wait":
            out.append(f"Wait: requires business quality ≥ {th['wait']['quality_min']} with entry conditions unmet"
                       + (" (Buy Now conditions were met)." if rec == "Buy Now" else f"; quality is {q}."))
        if rec != "Watchlist":
            out.append("Watchlist: used when the case is neither strong enough to wait for entry nor weak enough to reject; "
                       f"not chosen because the {rec} rule applied first.")
        if rec != "Reject":
            out.append(f"Reject: requires quality ≤ {th['reject']['quality_max']} or high risk with weak quality; quality is {q}, "
                       f"risk score {r}.")
        return out
