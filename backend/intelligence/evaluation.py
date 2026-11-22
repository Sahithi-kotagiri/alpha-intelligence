"""Evaluation: single-LLM baseline vs multi-agent platform, and a point-in-time backtest.

Baseline comparison metrics (computed identically for both systems on the SAME evidence pack):
- citation validity: share of cited IDs that exist in the evidence pack
- unverified numbers: numeric tokens in prose not matching any evidence value
- missing-data detection: share of the data-quality report's missing/conflicting fields acknowledged
- risk identification: number of distinct cited risks/objections
- consistency: recommendation agreement across repeated runs
- cost and latency: tokens and wall time
"""

from __future__ import annotations

import statistics
import time
from datetime import date
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from backend.agents.base import load_prompt
from backend.agents.guardrails import claim_texts, collect_evidence_ids, unverified_numbers
from backend.agents.llm import LLMClient
from backend.analysis.evidence import EvidenceItem, render_for_prompt
from backend.schemas.agents import Claim


class BaselineReport(BaseModel):
    recommendation: Literal["Strong Buy", "Buy", "Wait", "Watchlist", "Reject"]
    confidence_pct: float
    thesis: list[Claim]
    risks: list[Claim]
    bear_case: str
    missing_information: list[str]
    executive_summary: str


def _quality(obj: Any, items: list[EvidenceItem], dq: dict[str, Any]) -> dict[str, Any]:
    ids = {i.id for i in items}
    cited = [e for _, e in collect_evidence_ids(obj)]
    valid = [e for e in cited if e in ids]
    texts = claim_texts(obj)
    missing_text = " ".join(obj.get("missing_information", []) if isinstance(obj, dict) else []).lower() + " " + \
        " ".join(texts).lower()
    expected = [f for f in (dq.get("missing_critical_fields", []) + dq.get("missing_important_fields", []))]
    expected += [c["field"] for c in dq.get("conflicts", [])]
    acknowledged = [f for f in expected if f.replace("_", " ") in missing_text or f in missing_text]
    return {"citations": len(cited), "citation_validity": (len(valid) / len(cited)) if cited else None,
            "unverified_numbers": len(unverified_numbers(texts, items)),
            "missing_data_expected": len(expected),
            "missing_data_acknowledged": (len(acknowledged) / len(expected)) if expected else None}


def run_baseline(llm: LLMClient, analysis_result: dict[str, Any], items: list[EvidenceItem], repeats: int = 1) -> dict[str, Any]:
    system = load_prompt("common")[0] + "\n\n" + load_prompt("baseline_single")[0]
    ident = analysis_result["identity"]
    user = (f"Company: {ident['name']} ({ident['ticker']}). Horizon {analysis_result['params'].get('horizon')}, benchmark "
            f"{analysis_result['params'].get('benchmark')}.\n\nEvidence:\n{render_for_prompt(items)}")
    runs = []
    for _ in range(repeats):
        t0 = time.monotonic()
        res = llm.structured(system, user, BaselineReport)
        data = res.parsed.model_dump()
        q = _quality(data, items, analysis_result["data_quality"])
        q.update(recommendation=data["recommendation"], risks=len(data["risks"]), latency_s=time.monotonic() - t0,
                 input_tokens=res.input_tokens, output_tokens=res.output_tokens)
        runs.append(q)
    recs = [r["recommendation"] for r in runs]
    return {"runs": runs, "consistency": max(recs.count(x) for x in set(recs)) / len(recs) if recs else None}


def platform_quality(analysis_result: dict[str, Any], items: list[EvidenceItem]) -> dict[str, Any]:
    """Same metrics over the multi-agent output (all agent narratives + committee + final)."""
    obj = {"reports": analysis_result["reports"], "committee": analysis_result["committee"], "final": analysis_result["final"],
           "missing_information": [m for r in analysis_result["reports"].values() for m in r.get("missing_information", [])]}
    q = _quality(obj, items, analysis_result["data_quality"])
    da = analysis_result["reports"].get("devils_advocate", {})
    q["risks"] = len((da.get("deterministic") or {}).get("llm_objections") or (da.get("deterministic") or {}).get("objections") or [])
    q["recommendation"] = analysis_result["final"]["recommendation"]
    runs = analysis_result.get("model_runs", [])
    q["input_tokens"] = sum(r.get("input_tokens") or 0 for r in runs)
    q["output_tokens"] = sum(r.get("output_tokens") or 0 for r in runs)
    q["mode"] = analysis_result.get("mode")
    return q


def compare(platform: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    b = baseline["runs"]

    def mean(k):
        xs = [r[k] for r in b if r.get(k) is not None]
        return statistics.mean(xs) if xs else None

    rows = []
    for k, better in (("citation_validity", "higher"), ("unverified_numbers", "lower"), ("missing_data_acknowledged", "higher"),
                      ("risks", "higher"), ("input_tokens", "lower"), ("output_tokens", "lower")):
        rows.append({"metric": k, "platform": platform.get(k), "baseline_mean": mean(k), "better": better})
    return {"rows": rows, "baseline_consistency": baseline["consistency"], "platform_recommendation": platform["recommendation"],
            "baseline_recommendations": [r["recommendation"] for r in b],
            "note": "Single-company comparisons are anecdotal; run across many analyses before drawing conclusions. "
                    "If the multi-agent design does not improve these metrics, simplify it."}


# ------------------------------------------------------------------ point-in-time backtest
def pit_features(facts: dict, prices: pd.DataFrame, as_of: date) -> dict[str, Any] | None:
    """Features using only SEC filings filed on/before `as_of` and the close on/before `as_of`."""
    from backend.data.normalisation import FIELDS, sec_field_series

    ann_rev, _ = sec_field_series(facts, FIELDS["revenue"], as_of)
    ann_oi, _ = sec_field_series(facts, FIELDS["operating_income"], as_of)
    ann_eps, _ = sec_field_series(facts, FIELDS["diluted_eps"], as_of)
    if len(ann_rev) < 2 or not ann_eps:
        return None
    ends = sorted(ann_rev)
    r1, r0 = ann_rev[ends[-1]].value, ann_rev[ends[-2]].value
    if (ends[-1] - ends[-2]).days > 400 or r0 <= 0:
        return None
    eps_end = max(ann_eps)
    eps = ann_eps[eps_end].value
    oi = ann_oi.get(ends[-1])
    sub = prices.loc[: pd.Timestamp(as_of)]
    if sub.empty or (as_of - sub.index[-1].date()).days > 7:
        return None
    px = float(sub["Close"].iloc[-1])
    return {"revenue_growth": r1 / r0 - 1, "operating_margin": (oi.value / r1) if oi else None,
            "earnings_yield": eps / px, "price": px, "latest_filing": ann_rev[ends[-1]].filed.isoformat()}


def forward_excess(prices: pd.DataFrame, bench: pd.DataFrame, as_of: date, sessions: int = 252,
                   cost_bps: float = 10.0) -> float | None:
    s = _adj(prices)
    b = _adj(bench)
    s = s[s.index > pd.Timestamp(as_of)]
    if len(s) < sessions + 1:
        return None
    w = s.iloc[: sessions + 1]
    bw = b.reindex(w.index).ffill()
    if bw.isna().any():
        return None
    return float(w.iloc[-1] / w.iloc[0] - 1 - 2 * cost_bps / 1e4 - (bw.iloc[-1] / bw.iloc[0] - 1))


def _adj(df):
    return df["Adj Close"] if "Adj Close" in df else df["Close"]


def backtest(samples: list[dict[str, Any]], bench: pd.DataFrame, dates: list[date]) -> dict[str, Any]:
    """samples: [{ticker, facts, prices}]. Ranks by a simple composite (growth↑, margin↑, earnings yield↑)
    within each date, splits into terciles, and reports forward 1y excess returns per tercile."""
    rows = []
    for d in dates:
        feats = []
        for smp in samples:
            f = pit_features(smp["facts"], smp["prices"], d)
            if f is None or f["operating_margin"] is None:
                continue
            fx = forward_excess(smp["prices"], bench, d)
            if fx is None:
                continue
            feats.append({"ticker": smp["ticker"], "date": d.isoformat(), **f, "fwd_excess_1y": fx})
        if len(feats) < 6:
            continue
        df = pd.DataFrame(feats)
        df["score"] = (df["revenue_growth"].rank(pct=True) + df["operating_margin"].rank(pct=True)
                       + df["earnings_yield"].rank(pct=True)) / 3
        df["tercile"] = pd.qcut(df["score"].rank(method="first"), 3, labels=["low", "mid", "high"])
        rows.append(df)
    if not rows:
        return {"available": False, "reason": "insufficient point-in-time observations"}
    allr = pd.concat(rows)
    g = allr.groupby("tercile", observed=True)["fwd_excess_1y"]
    res = [{"tercile": str(k), "n": int(v.count()), "mean_excess": float(v.mean()), "median_excess": float(v.median()),
            "hit_rate": float((v > 0).mean())} for k, v in g]
    return {"available": True, "observations": len(allr), "dates": [d.isoformat() for d in dates], "terciles": res,
            "spread_high_minus_low": res[-1]["mean_excess"] - res[0]["mean_excess"] if len(res) == 3 else None,
            "caveats": ["Universe = current S&P 500 constituents: survivorship bias (delisted/removed companies excluded).",
                        "Fundamentals are point-in-time by SEC filing date; prices are split-adjusted vendor data.",
                        "Transaction cost of 10 bps per side deducted; no slippage model.",
                        "Small samples are not statistically significant."],
            "rows": allr.drop(columns=["price"]).to_dict("records")}
