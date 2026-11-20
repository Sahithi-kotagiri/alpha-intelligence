"""Transparent rubric scoring (0–10, higher = more favourable; for risk higher = lower risk)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.analysis.assessment import VERDICT_POINTS, Assessment
from backend.analysis.config_loader import rubrics


class DimensionScore(BaseModel):
    name: str
    weight: float
    score: float | None  # 0..10
    coverage: float  # share of metrics in the dimension with a usable verdict
    components: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None


class Score(BaseModel):
    name: str
    score: float | None
    direction: str = "higher = more favourable"
    rubric_version: str
    dimensions: list[DimensionScore]
    coverage: float
    unavailable_dimensions: list[str] = Field(default_factory=list)
    explanation: str = ""


def _finish(name: str, dims: list[DimensionScore], direction: str = "higher = more favourable") -> Score:
    usable = [d for d in dims if d.score is not None and d.coverage >= 0.5]
    tw = sum(d.weight for d in usable)
    score = round(sum(d.score * d.weight for d in usable) / tw, 2) if tw else None
    cov = round(tw / sum(d.weight for d in dims), 3) if dims else 0.0
    unav = [d.name for d in dims if d not in usable]
    parts = [f"{d.name.replace('_', ' ')} {d.score:.1f}/10 (weight {d.weight:.0%})" for d in usable]
    expl = (f"Weighted mean of usable dimensions: {', '.join(parts)}." if parts else "No usable dimensions.")
    if unav:
        expl += f" Excluded for insufficient data: {', '.join(unav)} (weights re-normalised)."
    return Score(name=name, score=score, direction=direction, rubric_version=rubrics()["version"], dimensions=dims,
                 coverage=cov, unavailable_dimensions=unav, explanation=expl)


def score_from_assessments(name: str, assessments: list[Assessment]) -> Score:
    cfg = rubrics()[name]
    by_key = {a.metric_key: a for a in assessments}
    dims = []
    for dname, d in cfg.items():
        comps, pts = [], []
        for k in d["metrics"]:
            a = by_key.get(k)
            if a is None:
                comps.append({"metric": k, "verdict": "insufficient_data", "points": None})
                continue
            p = VERDICT_POINTS.get(a.verdict)
            comps.append({"metric": k, "label": a.label, "verdict": a.verdict, "points": p, "display": a.display,
                          "evidence_id": a.metric_id})
            if a.verdict == "not_applicable":
                continue
            if p is not None:
                pts.append(p)
        applicable = [c for c in comps if c["verdict"] != "not_applicable"]
        cov = len(pts) / len(applicable) if applicable else 0.0
        dims.append(DimensionScore(name=dname, weight=d["weight"], score=round(10 * sum(pts) / len(pts), 2) if pts else None,
                                   coverage=round(cov, 3), components=comps))
    return _finish(name, dims)


def _pts(cond_fav: bool | None, cond_unf: bool | None) -> float | None:
    if cond_fav is None and cond_unf is None:
        return None
    if cond_fav:
        return 1.0
    if cond_unf:
        return 0.0
    return 0.5


def technical_score(t: dict[str, Any]) -> Score:
    """Rules (documented, METHODOLOGY §5): each rule scores 1 / 0.5 / 0."""
    cfg = rubrics()["technical"]
    trend_map = {"up": 1.0, "sideways": 0.5, "down": 0.0}
    tr = [trend_map.get(t.get(k)) for k in ("trend_short", "trend_medium", "trend_long")]
    tr_c = [{"rule": f"{k.replace('trend_', '')}-term trend", "value": t.get(k), "points": p}
            for k, p in zip(("trend_short", "trend_medium", "trend_long"), tr, strict=False)]
    if t.get("golden_cross_state"):
        p = 1.0 if t["golden_cross_state"] == "50 above 200" else 0.0
        tr.append(p)
        tr_c.append({"rule": "50-day vs 200-day average", "value": t["golden_cross_state"], "points": p})
    rsi = t.get("rsi_14")
    mom = []
    mom_c = []
    if rsi is not None:
        p = 1.0 if 50 <= rsi <= 70 else 0.5 if (40 <= rsi < 50 or 70 < rsi <= 80) else 0.0
        mom.append(p)
        mom_c.append({"rule": "RSI(14): 50–70 constructive; >80 overbought; <40 weak", "value": round(rsi, 1), "points": p})
    if t.get("macd_hist") is not None:
        rising = t.get("macd_hist_5d_ago") is not None and t["macd_hist"] > t["macd_hist_5d_ago"]
        p = 1.0 if (t["macd_hist"] > 0 and rising) else 0.5 if (t["macd_hist"] > 0 or rising) else 0.0
        mom.append(p)
        mom_c.append({"rule": "MACD histogram positive and rising", "value": round(t["macd_hist"], 3), "points": p})
    rs = []
    rs_c = []
    for k in ("3m", "6m", "12m"):
        v = (t.get("rs_benchmark") or {}).get(k)
        if v is not None:
            p = _pts(v > 0.02, v < -0.02)
            rs.append(p)
            rs_c.append({"rule": f"{k} return vs benchmark (±2% neutral band)", "value": round(v, 4), "points": p})
    vs = (t.get("rs_sector") or {}).get("6m")
    if vs is not None:
        p = _pts(vs > 0.02, vs < -0.02)
        rs.append(p)
        rs_c.append({"rule": "6m return vs sector ETF", "value": round(vs, 4), "points": p})
    vol = []
    vol_c = []
    if t.get("volume_trend") is not None:
        up = t.get("trend_medium") == "up"
        vt = t["volume_trend"]
        p = 1.0 if (up and vt > 0) else 0.5 if abs(vt) <= 0.1 or (not up and vt < 0) else 0.0
        vol.append(p)
        vol_c.append({"rule": "volume confirms medium-term trend (20d vs 50d average volume)", "value": round(vt, 3), "points": p})
    if t.get("breakout") in ("confirmed", "possible"):
        p = 1.0 if t["breakout"] == "confirmed" else 0.5
        vol.append(p)
        vol_c.append({"rule": "breakout above prior 50-session high", "value": t["breakout"], "points": p})

    def dim(name, xs, comps):
        xs = [x for x in xs if x is not None]
        return DimensionScore(name=name, weight=cfg[name]["weight"], score=round(10 * sum(xs) / len(xs), 2) if xs else None,
                              coverage=1.0 if xs else 0.0, components=comps)

    return _finish("technical", [dim("trend", tr, tr_c), dim("momentum", mom, mom_c), dim("relative_strength", rs, rs_c),
                                 dim("volume", vol, vol_c)])


def risk_score(r: dict[str, Any], assessments: list[Assessment]) -> Score:
    """Higher = LOWER risk. Thresholds documented in METHODOLOGY §7."""
    cfg = rubrics()["risk"]
    by = {a.metric_key: a for a in assessments}

    def band(v, good, bad, higher_is_riskier=True):
        if v is None:
            return None
        if higher_is_riskier:
            return 1.0 if v <= good else 0.0 if v >= bad else 0.5
        return 1.0 if v >= good else 0.0 if v <= bad else 0.5

    vol = r.get("vol_1y")
    bvol = r.get("benchmark_vol_1y")
    rel = vol / bvol if (vol and bvol) else None
    v_p = band(rel, 1.2, 2.0)
    mdd = r.get("max_drawdown_3y")
    d_p = band(abs(mdd) if mdd is not None else None, 0.25, 0.50)
    lev = [VERDICT_POINTS.get(by[k].verdict) for k in ("debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "altman_z")
           if k in by and by[k].verdict in VERDICT_POINTS]
    b = r.get("beta_2y")
    m_p = band(b, 1.1, 1.6)
    dc = r.get("downside_capture")
    m2 = band(dc, 0.9, 1.3) if dc is not None else None
    liq = r.get("avg_dollar_volume_20d")
    l_p = band(liq, 50e6, 5e6, higher_is_riskier=False)
    gaps = (r.get("gaps") or {}).get("gaps_over_threshold")
    g_p = band(gaps, 2, 8) if gaps is not None else None

    def dim(name, xs, comps):
        xs = [x for x in xs if x is not None]
        return DimensionScore(name=name, weight=cfg[name]["weight"], score=round(10 * sum(xs) / len(xs), 2) if xs else None,
                              coverage=1.0 if xs else 0.0, components=comps)

    return _finish("risk", [
        dim("volatility", [v_p], [{"rule": "1y volatility relative to benchmark: ≤1.2x low risk, ≥2.0x high risk",
                                   "value": None if rel is None else round(rel, 2), "points": v_p}]),
        dim("drawdown", [d_p, g_p], [{"rule": "3y max drawdown: ≤25% low, ≥50% high", "value": mdd, "points": d_p},
                                     {"rule": "opening gaps >5% in 2y: ≤2 low, ≥8 high", "value": gaps, "points": g_p}]),
        dim("leverage", lev, [{"rule": "leverage assessments (D/E, ND/EBITDA, coverage, Altman Z)",
                               "value": {k: by[k].verdict for k in ("debt_to_equity", "net_debt_to_ebitda",
                                                                    "interest_coverage", "altman_z") if k in by},
                               "points": None}]),
        dim("market", [m_p, m2], [{"rule": "2y beta: ≤1.1 low, ≥1.6 high", "value": b, "points": m_p},
                                  {"rule": "downside capture: ≤0.9 low, ≥1.3 high", "value": dc, "points": m2}]),
        dim("liquidity", [l_p], [{"rule": "20d average dollar volume: ≥$50M low risk, ≤$5M high", "value": liq, "points": l_p}]),
    ], direction="higher = lower risk")


def risk_rating(score: float | None) -> str:
    if score is None:
        return "Unknown"
    if score >= 7.5:
        return "Low"
    if score >= 5.5:
        return "Moderate"
    if score >= 3.5:
        return "High"
    return "Very High"
