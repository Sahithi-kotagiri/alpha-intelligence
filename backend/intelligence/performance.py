"""Recommendation performance tracking.

Convention (METHODOLOGY §10): entry = adjusted close on the decision's price date (the
latest price available at decision time; no look-ahead). Horizon N = N trading sessions
after entry (21/63/126/252 ≈ 1/3/6/12 months). Returns use adjusted closes (dividends
reinvested: a total-return proxy). Excess = stock − benchmark over the same sessions.
Hit definitions: Strong Buy / Buy (and legacy Buy Now) → excess > 0; Reject → excess < 0; Wait and Watchlist are
reported but not hit-scored (they are not directional calls on the stock).
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select

from backend.database import models as M
from backend.tools.technical_indicators import annualised_vol, max_drawdown

log = logging.getLogger(__name__)
SMALL_SAMPLE = 30


def _adj(df: pd.DataFrame) -> pd.Series:
    return df["Adj Close"] if "Adj Close" in df else df["Close"]


def observe(stock: pd.DataFrame, bench: pd.DataFrame | None, start_date, sessions: int) -> dict[str, Any] | None:
    s = _adj(stock)
    s = s[s.index >= pd.Timestamp(start_date)]
    if len(s) < sessions + 1:
        return None
    window = s.iloc[: sessions + 1]
    out = {"start_date": window.index[0].date(), "end_date": window.index[-1].date(), "start_price": float(window.iloc[0]),
           "end_price": float(window.iloc[-1]), "total_return": float(window.iloc[-1] / window.iloc[0] - 1),
           "max_drawdown": max_drawdown(window)[0], "volatility": annualised_vol(window),
           "price_basis": "adjusted close (total-return proxy)"}
    if bench is not None and not bench.empty:
        b = _adj(bench).reindex(window.index).ffill()
        if b.notna().all():
            out["benchmark_return"] = float(b.iloc[-1] / b.iloc[0] - 1)
            out["excess_return"] = out["total_return"] - out["benchmark_return"]
    return out


def update_performance(session_factory, providers) -> dict[str, Any]:
    done = pending = 0
    cache: dict[str, pd.DataFrame | None] = {}

    def prices(t):
        if t not in cache:
            try:
                cache[t] = providers.market.get_price_history(t)
            except Exception as exc:
                log.info("performance: no prices for %s: %s", t, exc)
                cache[t] = None
        return cache[t]

    with session_factory() as db:
        obs = db.scalars(select(M.PerformanceObservation).where(M.PerformanceObservation.status == "pending")).all()
        for o in obs:
            rec = db.get(M.Recommendation, o.recommendation_id)
            if rec is None or rec.price_date is None:
                continue
            st, bm = prices(rec.ticker), prices(rec.benchmark or "SPY")
            if st is None:
                pending += 1
                continue
            r = observe(st, bm, rec.price_date, o.horizon_sessions)
            if r is None:
                pending += 1
                continue
            for k, v in r.items():
                setattr(o, k, v)
            o.status, o.computed_at = "complete", datetime.now(UTC)
            done += 1
        db.commit()
    return {"completed": done, "still_pending": pending}


def summary(db, user_id: int) -> dict[str, Any]:
    rows = db.execute(select(M.Recommendation, M.PerformanceObservation)
                      .join(M.PerformanceObservation, M.PerformanceObservation.recommendation_id == M.Recommendation.id)
                      .where(M.Recommendation.user_id == user_id)).all()
    by: dict[tuple[str, str], list[float]] = {}
    hits: dict[tuple[str, str], list[bool]] = {}
    bands: dict[tuple[str, str], list[float]] = {}
    sectors: dict[tuple[str, str], list[float]] = {}
    acted: dict[str, list[float]] = {}
    pending = 0
    for rec, o in rows:
        if o.status != "complete" or o.excess_return is None:
            pending += 1
            continue
        k = (rec.recommendation, o.horizon_label)
        by.setdefault(k, []).append(o.excess_return)
        if rec.recommendation in ("Strong Buy", "Buy", "Buy Now"):
            hits.setdefault(k, []).append(o.excess_return > 0)
        elif rec.recommendation == "Reject":
            hits.setdefault(k, []).append(o.excess_return < 0)
        c = (rec.scores or {}).get("composite")
        band = "unknown" if c is None else "composite <50" if c < 50 else "composite 50–75" if c < 75 else "composite ≥75"
        bands.setdefault((band, o.horizon_label), []).append(o.excess_return)
        sectors.setdefault((rec.sector or "Unknown", o.horizon_label), []).append(o.excess_return)
        acted.setdefault(rec.user_action, []).append(o.excess_return)

    def agg(d):
        return [{"group": k[0], "horizon": k[1], "n": len(v), "mean_excess": statistics.mean(v),
                 "median_excess": statistics.median(v),
                 "hit_rate": (sum(hits.get(k, [])) / len(hits[k])) if hits.get(k) else None,
                 "small_sample": len(v) < SMALL_SAMPLE} for k, v in sorted(d.items())]

    n_complete = sum(len(v) for v in by.values())
    return {"by_recommendation": agg(by), "by_quality_band": agg(bands), "by_sector": agg(sectors),
            "by_user_action": [{"action": k, "n": len(v), "mean_excess": statistics.mean(v)} for k, v in acted.items()],
            "observations_complete": n_complete, "observations_pending": pending,
            "caveats": ["Results are descriptive. With fewer than 30 observations per group no statistical significance is claimed.",
                        "Hit rate: Strong Buy/Buy = excess return > 0; Reject = excess return < 0; Wait/Watchlist not hit-scored.",
                        "Returns use adjusted closes from the decision's price date; no transaction costs are deducted here.",
                        "Recommendation quality is confounded by market direction; compare excess, not absolute, returns."]}
