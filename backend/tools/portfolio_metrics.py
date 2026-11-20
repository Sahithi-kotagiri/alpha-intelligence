"""Portfolio analytics: weights, concentration, correlation, volatility, drawdown, candidate impact.

Method notes (also in docs/METHODOLOGY.md §8):
- Returns: daily simple returns of adjusted closes, aligned on common dates, lookback 1 year.
- Portfolio volatility: sqrt(wᵀ Σ w) × √252 with constant weights (no rebalancing drift).
- Historical drawdown: constant-weight daily-rebalanced portfolio (an approximation).
- Candidate impact: candidate added at weight w, existing risky holdings scaled by (1 − w)
  (i.e. funded proportionally); cash is treated as zero-volatility.
- Concentration: Herfindahl–Hirschman index (Σ w²) and effective number of positions (1/HHI).
Historical correlations are not guarantees of future correlations.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def weights_from_holdings(holdings: list[dict[str, Any]], prices: dict[str, float | None], cash: float = 0.0) -> dict[str, Any]:
    """holdings: [{ticker, quantity?, market_value?, weight?}]. Returns weights incl. cash and any missing items."""
    values: dict[str, float] = {}
    missing: list[str] = []
    explicit_w = {h["ticker"]: h["weight"] for h in holdings if h.get("weight") is not None
                  and h.get("quantity") is None and h.get("market_value") is None}
    for h in holdings:
        t = h["ticker"]
        if h.get("market_value") is not None:
            values[t] = float(h["market_value"])
        elif h.get("quantity") is not None:
            p = prices.get(t)
            if p is None:
                missing.append(t)
                continue
            values[t] = float(h["quantity"]) * p
    if values and not explicit_w:
        total = sum(values.values()) + cash
        if total <= 0:
            return {"weights": {}, "cash_weight": 0.0, "total_value": 0.0, "missing_prices": missing, "method": "value"}
        return {"weights": {t: v / total for t, v in values.items()}, "cash_weight": cash / total,
                "total_value": total, "missing_prices": missing, "method": "market value"}
    if explicit_w:
        tw = sum(explicit_w.values())
        if tw > 1.0001 and tw <= 100.5:  # supplied as percentages
            explicit_w = {t: w / 100 for t, w in explicit_w.items()}
            tw /= 100
        cash_w = max(0.0, 1 - tw)
        return {"weights": explicit_w, "cash_weight": cash_w, "total_value": None, "missing_prices": missing,
                "method": "user-supplied weights"}
    return {"weights": {}, "cash_weight": 1.0 if cash else 0.0, "total_value": cash, "missing_prices": missing, "method": "none"}


def hhi(weights: dict[str, float]) -> float:
    return float(sum(w * w for w in weights.values()))


def returns_frame(price_map: dict[str, pd.DataFrame], sessions: int = 252) -> pd.DataFrame:
    cols = {}
    for t, df in price_map.items():
        if df is None or df.empty:
            continue
        s = df["Adj Close"] if "Adj Close" in df else df["Close"]
        cols[t] = s.pct_change()
    if not cols:
        return pd.DataFrame()
    r = pd.DataFrame(cols).dropna(how="any")
    return r.iloc[-sessions:]


def portfolio_vol(weights: dict[str, float], rets: pd.DataFrame) -> float | None:
    tick = [t for t in weights if t in rets.columns]
    if not tick or len(rets) < 60:
        return None
    w = np.array([weights[t] for t in tick])
    cov = rets[tick].cov().values
    return float(math.sqrt(max(w @ cov @ w, 0)) * math.sqrt(252))


def portfolio_drawdown(weights: dict[str, float], rets: pd.DataFrame) -> float | None:
    tick = [t for t in weights if t in rets.columns]
    if not tick or len(rets) < 20:
        return None
    pr = (rets[tick] * pd.Series({t: weights[t] for t in tick})).sum(axis=1)
    nav = (1 + pr).cumprod()
    return float((nav / nav.cummax() - 1).min())


def portfolio_beta(weights: dict[str, float], rets: pd.DataFrame, bench_rets: pd.Series) -> float | None:
    tick = [t for t in weights if t in rets.columns]
    j = pd.concat([(rets[tick] * pd.Series({t: weights[t] for t in tick})).sum(axis=1), bench_rets], axis=1,
                  join="inner").dropna()
    if len(j) < 60 or j.iloc[:, 1].var() == 0:
        return None
    return float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var())


def risk_contributions(weights: dict[str, float], rets: pd.DataFrame) -> dict[str, float]:
    tick = [t for t in weights if t in rets.columns]
    if not tick or len(rets) < 60:
        return {}
    w = np.array([weights[t] for t in tick])
    cov = rets[tick].cov().values
    var = w @ cov @ w
    if var <= 0:
        return {}
    mrc = cov @ w
    return {t: float(w[i] * mrc[i] / var) for i, t in enumerate(tick)}


def exposures(weights: dict[str, float], attr: dict[str, str | None]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t, w in weights.items():
        k = attr.get(t) or "Unknown"
        out[k] = out.get(k, 0.0) + w
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def candidate_impact(weights: dict[str, float], cash_w: float, candidate: str, cand_w: float,
                     rets: pd.DataFrame, sectors: dict[str, str | None]) -> dict[str, Any]:
    """Compare portfolio before/after adding `candidate` at `cand_w`, funded proportionally."""
    before = dict(weights)
    scale = 1 - cand_w
    after = {t: w * scale for t, w in weights.items()}
    after[candidate] = after.get(candidate, 0.0) + cand_w
    out: dict[str, Any] = {"candidate": candidate, "candidate_weight": cand_w,
                           "funding": "existing positions and cash scaled proportionally",
                           "cash_weight_before": cash_w, "cash_weight_after": cash_w * scale}
    out["vol_before"], out["vol_after"] = portfolio_vol(before, rets), portfolio_vol(after, rets)
    out["max_drawdown_before"], out["max_drawdown_after"] = portfolio_drawdown(before, rets), portfolio_drawdown(after, rets)
    out["hhi_before"], out["hhi_after"] = hhi(before), hhi(after)
    out["effective_positions_before"] = 1 / out["hhi_before"] if out["hhi_before"] else None
    out["effective_positions_after"] = 1 / out["hhi_after"] if out["hhi_after"] else None
    out["sector_before"], out["sector_after"] = exposures(before, sectors), exposures(after, sectors)
    if candidate in rets.columns:
        corrs = rets.corr()[candidate].drop(candidate, errors="ignore")
        out["candidate_correlations"] = {t: round(float(v), 3) for t, v in corrs.items() if t in weights}
        if weights:
            pr = (rets[[t for t in weights if t in rets.columns]] *
                  pd.Series({t: w for t, w in weights.items() if t in rets.columns})).sum(axis=1)
            out["candidate_corr_to_portfolio"] = float(rets[candidate].corr(pr)) if len(pr) > 60 else None
    out["risk_contribution_after"] = risk_contributions(after, rets)
    out["lookback_sessions"] = len(rets)
    return out


def correlation_matrix(rets: pd.DataFrame) -> dict[str, Any]:
    if rets.empty:
        return {"tickers": [], "matrix": []}
    c = rets.corr()
    return {"tickers": list(c.columns), "matrix": [[round(float(x), 3) for x in row] for row in c.values],
            "lookback_sessions": len(rets), "frequency": "daily"}
