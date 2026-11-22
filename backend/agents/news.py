"""News & Sentiment Analyst: classifies dated, source-linked news and SEC filings.

Sentiment and materiality classification of free text is interpretation and requires
the LLM. Without one, items are listed with dates and links (and 8-K items with their
rule-based materiality) but no sentiment score is produced – no keyword heuristics.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from backend.agents.base import AgentContext, BaseAgent
from backend.analysis.config_loader import rubrics
from backend.analysis.evidence import scoped
from backend.analysis.scoring import DimensionScore, Score
from backend.schemas.agents import AgentReportOut, Claim, NewsNarrative


def news_score(classifications: list[dict[str, Any]]) -> Score:
    """score = 5 + 5 × Σ(sign × weight) / Σ(weight); sign: +1 positive, −1 negative, 0 mixed/neutral."""
    w = rubrics()["news"]["materiality_weights"]
    sign = {"positive": 1, "negative": -1, "mixed": 0, "neutral": 0}
    items = [c for c in classifications if not c.get("duplicate_of")]
    tot = sum(w[c["materiality"]] for c in items)
    val = None if not tot else round(5 + 5 * sum(sign[c["sentiment"]] * w[c["materiality"]] for c in items) / tot, 2)
    dim = DimensionScore(name="sentiment_x_materiality", weight=1.0, score=val, coverage=1.0 if items else 0.0,
                         components=[{"evidence_id": c["evidence_id"], "sentiment": c["sentiment"],
                                      "materiality": c["materiality"]} for c in items])
    return Score(name="news", score=val, rubric_version=rubrics()["version"], dimensions=[dim], coverage=dim.coverage,
                 explanation="5 + 5 × materiality-weighted net sentiment (high=3, medium=2, low=1); duplicates excluded."
                 if val is not None else "No classified items.")


class NewsAnalyst(BaseAgent):
    name = "news"
    title = "News & Estimates Analyst"
    responsibility = ("Recent company, filing and analyst developments and their materiality, plus the rulebook revisions "
                      "pillar: 90-day consensus EPS revisions and the last four earnings surprises.")
    prompt_name = "news"
    schema = NewsNarrative

    def scope(self, ctx: AgentContext):
        return scoped(ctx.pack, "news")

    def deterministic(self, ctx: AgentContext) -> dict[str, Any]:
        news = ctx.pack.by_category("news")
        filings = ctx.pack.by_category("filing")
        today = date.today()
        recent_8k = [f for f in filings if f.detail.get("form", "").startswith("8-K") and f.date and
                     (today - date.fromisoformat(f.date)).days <= 120]
        high = [f for f in recent_8k if f.detail.get("materiality") == "high"]
        claims = [Claim(text=f"{f.title}.", kind="fact", evidence_ids=[f.id]) for f in recent_8k[:8]]
        aa = ctx.pack.by_category("analyst_action")
        summary = (f"{len(news)} vendor news items and {len(recent_8k)} 8-K filings in the last 120 days "
                   f"({len(high)} with high-materiality items). Sentiment and materiality of news text require "
                   "LLM classification and were not scored.")
        weaknesses = [Claim(text=f"High-materiality filing: {f.title}.", kind="fact", evidence_ids=[f.id]) for f in high]
        tables = {"news": [{"id": i.id, "date": i.date, "publisher": i.source_name, "url": i.source_url,
                            "headline": (i.untrusted_text or "").split("\n")[0]} for i in news],
                  "filings": [{"id": f.id, "date": f.date, "form": f.detail.get("form"), "items": f.detail.get("items"),
                               "materiality": f.detail.get("materiality"), "url": f.source_url} for f in filings[:25]],
                  "analyst_actions": [{"id": a.id, "date": a.date, "title": a.title} for a in aa],
                  "upcoming": [{"id": i.id, "title": i.title, "date": i.date} for i in ctx.pack.by_category("calendar")],
                  "high_materiality_filings": [f.id for f in high]}
        rev = ((ctx.rulebook or {}).get("pillars") or {}).get("revisions") or {}
        strengths = []
        for mm in rev.get("measures", []):
            if mm.get("score") is None:
                continue
            c = Claim(text=f"{mm['label']}: {mm['value'] * 100:+.1f}% → score {mm['score']:.0f}/100 ({mm['basis']}).",
                      kind="fact", evidence_ids=[f"rb.m.{mm['key']}"])
            (strengths if mm["score"] >= 60 else weaknesses if mm["score"] <= 40 else claims).append(c)
        if rev.get("score") is not None:
            summary = f"Revisions pillar {rev['score']:.0f}/100 (estimate revisions and earnings surprises). " + summary
        tables["revisions_pillar"] = rev
        score = None
        if rev.get("score") is not None and not rev.get("sparse"):
            score = Score(name="news", score=round(rev["score"] / 10, 2), rubric_version=(ctx.rulebook or {}).get("rulebook_version", ""),
                          dimensions=[DimensionScore(name="revisions_pillar", weight=1.0, score=round(rev["score"] / 10, 2),
                                                     coverage=1 - rev.get("missing_ratio", 0), components=rev.get("measures", []))],
                          coverage=1 - rev.get("missing_ratio", 0),
                          explanation="Rule-based mode: revisions pillar ÷ 10 (EPS revisions, earnings surprises). With an "
                                      "LLM, news sentiment × materiality is scored instead and the pillar is shown alongside.")
        return {"score": score, "summary": summary, "claims": claims, "weaknesses": weaknesses, "strengths": strengths,
                "missing": ["News sentiment/materiality classification (requires LLM)",
                            "Coverage is limited to the Yahoo news feed and SEC filings; not comprehensive news coverage"],
                "tables": tables, "confidence": ("Low", "News not classified; only filings and dated headlines listed.")}

    def apply_narrative(self, report: AgentReportOut, n: dict[str, Any], ctx: AgentContext) -> None:
        super().apply_narrative(report, n, ctx)
        valid = {i.id for i in self.scope(ctx)}
        cls = [c for c in n.get("classifications", []) if c.get("evidence_id") in valid]
        s = news_score(cls)
        report.score = s.model_dump()
        report.strengths = [Claim(**c) for c in n.get("positive_catalysts", [])]
        report.weaknesses = [Claim(**c) for c in n.get("negative_catalysts", [])]
        report.claims = [Claim(**c) for c in n.get("mixed_or_neutral", []) + n.get("upcoming_events", [])]
        report.missing_information = sorted(set(n.get("coverage_limitations", []) + [
            m for m in report.missing_information if "requires LLM" not in m]))
        report.deterministic["classifications"] = cls
        rev = report.deterministic.get("revisions_pillar") or {}
        if rev.get("score") is not None:
            report.claims.insert(0, Claim(text=f"Revisions pillar {rev['score']:.0f}/100 (rule-based).", kind="fact",
                                          evidence_ids=["rb.pillar.revisions"]))
