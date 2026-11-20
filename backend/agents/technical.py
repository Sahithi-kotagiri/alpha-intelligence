"""Technical Analyst: trend, momentum, relative strength, volume, volatility, support/resistance."""

from __future__ import annotations

from typing import Any

from backend.agents.base import AgentContext, BaseAgent, confidence_from
from backend.analysis.evidence import scoped
from backend.schemas.agents import AgentReportOut, Claim, TechnicalNarrative
from backend.tools.types import fmt_value


def entry_zone(t: dict[str, Any]) -> dict[str, Any]:
    """Nearest support zone within 15% below price, only when the long-term trend is not down."""
    price = t.get("price")
    sup = [z for z in (t.get("levels") or []) if z["type"] == "support" and -15 <= z["distance_pct"] < 0]
    if not price or not sup:
        return {"justified": False, "reason": "no support zone within 15% below the current price"}
    z = max(sup, key=lambda z: z["distance_pct"])
    if t.get("trend_long") == "down":
        return {"justified": False, "reason": "long-term trend is down; support zones are less reliable in downtrends",
                "nearest_support": z}
    return {"justified": True, "low": z["low"], "high": z["high"], "touches": z["touches"],
            "method": "nearest swing-point support cluster within 15% below price; estimate, not a guaranteed level"}


class TechnicalAnalyst(BaseAgent):
    name = "technical"
    title = "Technical Analyst"
    responsibility = "Price trend, momentum, relative strength, volatility and potential entry conditions."
    prompt_name = "technical"
    schema = TechnicalNarrative

    def scope(self, ctx: AgentContext):
        return scoped(ctx.pack, "technical")

    def deterministic(self, ctx: AgentContext) -> dict[str, Any]:
        t = ctx.technicals
        s = ctx.scores.get("technical")
        if not t or s is None:
            return {"score": None, "summary": "Price history unavailable; technical analysis not possible.",
                    "missing": ["daily price history"], "confidence": ("Low", "No price data."), "tables": {}}
        ez = entry_zone(t)
        inval = None
        if t.get("sma_200"):
            inval = f"Daily close below the 200-day moving average (${t['sma_200']:,.2f})"
        sup = [z for z in (t.get("levels") or []) if z["type"] == "support"]
        if ez.get("justified"):
            inval = f"Daily close below the support zone low (${ez['low']:,.2f})"
        claims = [
            Claim(text=f"Trend: short {t['trend_short']}, medium {t['trend_medium']}, long {t['trend_long']}; "
                       f"{t.get('golden_cross_state') or '50/200-day comparison unavailable'}.", kind="interpretation",
                  evidence_ids=["tech.trend_short", "tech.trend_medium", "tech.trend_long"]),
            Claim(text=f"RSI(14) {t['rsi_14']:.1f}; MACD histogram {t['macd_hist']:.3f}." if t.get("rsi_14") is not None
                  and t.get("macd_hist") is not None else "Momentum indicators unavailable.", kind="fact",
                  evidence_ids=["tech.rsi_14", "tech.macd_hist"] if t.get("rsi_14") is not None else []),
        ]
        rs = t.get("rs_benchmark") or {}
        if rs.get("6m") is not None:
            claims.append(Claim(text=f"6-month return vs benchmark: {fmt_value(rs['6m'], 'pct')}; 12-month: "
                                     f"{fmt_value(rs.get('12m'), 'pct')}.", kind="fact",
                                evidence_ids=["tech.rs_benchmark.6m"] + (["tech.rs_benchmark.12m"] if rs.get("12m") is not None else [])))
        claims.append(Claim(text=f"Breakout status: {t['breakout']} (close vs prior 50-session high"
                                 f"{' $' + format(t['prior_50d_high'], ',.2f') if t.get('prior_50d_high') else ''}).",
                            kind="interpretation", evidence_ids=["tech.breakout"]))
        strengths = [c for c in claims if "up" in c.text and "Trend" in c.text]
        weaknesses = [c for c in claims if "down" in c.text and "Trend" in c.text]
        risk_level = "high" if (t.get("atr_pct") or 0) > 0.035 else "medium" if (t.get("atr_pct") or 0) > 0.02 else "low"
        missing = []
        if t.get("sma_200") is None:
            missing.append("Fewer than 200 sessions of history: long-term trend unavailable.")
        if t.get("rs_benchmark") is None:
            missing.append("Benchmark prices unavailable: relative strength not computed.")
        conf = confidence_from(s.coverage, ctx.dq.status)
        summary = (f"Technical score {s.score}/10. Trend short/medium/long: {t['trend_short']}/{t['trend_medium']}/"
                   f"{t['trend_long']}. Entry zone {'justified' if ez.get('justified') else 'not justified'}: "
                   f"{ez.get('reason') or ('$%.2f–$%.2f' % (ez['low'], ez['high']))}. Volatility risk {risk_level} "
                   f"(ATR {fmt_value(t.get('atr_pct'), 'pct')} of price).")
        tables = {"indicators": {k: t.get(k) for k in ("price", "sma_20", "sma_50", "sma_100", "sma_200", "dist_sma_50",
                                                        "dist_sma_200", "rsi_14", "macd", "macd_signal", "macd_hist", "atr_14",
                                                        "atr_pct", "vol_20d", "vol_1y", "high_52w", "low_52w",
                                                        "drawdown_from_52w_high", "volume_trend", "breakout")},
                  "returns": t.get("returns"), "relative_strength": {"benchmark": t.get("rs_benchmark"), "sector": t.get("rs_sector")},
                  "levels": t.get("levels"), "levels_method": "Swing highs/lows (±10 sessions) over 252 sessions, clustered within 1×ATR(14)",
                  "entry_zone": ez, "invalidation": inval, "risk_level": risk_level, "data_through": t.get("data_through"),
                  "unusual_volume_days": t.get("unusual_volume_days_20d"), "rsi_period": 14, "macd_params": "12/26/9"}
        return {"score": s, "summary": summary, "claims": claims, "strengths": strengths, "weaknesses": weaknesses,
                "missing": missing, "tables": tables, "confidence": conf}

    def context_text(self, ctx: AgentContext, det: dict[str, Any]) -> str:
        s = ctx.scores.get("technical")
        return (f"Technical score {s.score if s else None}/10 ({s.explanation if s else ''}). "
                f"Rule-based entry zone: {det['tables'].get('entry_zone')}. Rule-based invalidation: {det['tables'].get('invalidation')}.")

    def apply_narrative(self, report: AgentReportOut, n: dict[str, Any], ctx: AgentContext) -> None:
        super().apply_narrative(report, n, ctx)
        report.summary = n["trend_summary"]["text"]
        report.claims = [Claim(**n[k]) for k in ("trend_summary", "momentum_assessment", "relative_strength_assessment",
                                                 "volume_assessment", "breakout_assessment", "entry_zone_rationale",
                                                 "invalidation_condition")]
        if not n.get("entry_zone_justified"):
            report.deterministic["entry_zone_llm_view"] = "LLM did not consider an entry zone justified."

    def deterministic_review(self, ctx: AgentContext, question: str) -> dict[str, Any]:
        t = ctx.technicals or {}
        if not t:
            return super().deterministic_review(ctx, question)
        return {"answer": f"Trend short/medium/long: {t.get('trend_short')}/{t.get('trend_medium')}/{t.get('trend_long')}; "
                          f"technical signals describe weeks-to-months timing, not business value.",
                "evidence_ids": ["tech.trend_short", "tech.trend_medium", "tech.trend_long"], "resolved": True,
                "cannot_resolve_reason": "", "mode": "deterministic"}
