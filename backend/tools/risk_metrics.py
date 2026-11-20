"""Standalone risk metrics: volatility, beta, drawdown, capture ratios, VaR, gaps, liquidity."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from backend.tools.technical_indicators import annualised_vol, max_drawdown


def _adj(df: pd.DataFrame) -> pd.Series:
    return df["Adj Close"] if "Adj Close" in df else df["Close"]


def beta(stock: pd.Series, bench: pd.Series, sessions: int = 504) -> tuple[float | None, int]:
    """OLS beta of daily simple returns over the last `sessions` overlapping days."""
    r = pd.concat([stock.pct_change(), bench.pct_change()], axis=1, join="inner").dropna().iloc[-sessions:]
    if len(r) < 60:
        return None, len(r)
    var = r.iloc[:, 1].var(ddof=1)
    if var == 0:
        return None, len(r)
    return float(r.iloc[:, 0].cov(r.iloc[:, 1]) / var), len(r)


def capture_ratios(stock: pd.Series, bench: pd.Series) -> tuple[float | None, float | None, int]:
    """Up/down capture on monthly returns: mean stock return in up/down benchmark months ÷ mean benchmark return."""
    m = pd.concat([stock.resample("ME").last().pct_change(), bench.resample("ME").last().pct_change()],
                  axis=1, join="inner").dropna()
    if len(m) < 12:
        return None, None, len(m)
    up, down = m[m.iloc[:, 1] > 0], m[m.iloc[:, 1] < 0]
    upc = float(up.iloc[:, 0].mean() / up.iloc[:, 1].mean()) if len(up) >= 3 else None
    dnc = float(down.iloc[:, 0].mean() / down.iloc[:, 1].mean()) if len(down) >= 3 else None
    return upc, dnc, len(m)


def historical_var(stock: pd.Series, level: float = 0.95, sessions: int = 504) -> tuple[float | None, float | None]:
    r = stock.pct_change().dropna().iloc[-sessions:]
    if len(r) < 100:
        return None, None
    q = float(np.quantile(r, 1 - level))
    cvar = float(r[r <= q].mean())
    return q, cvar


def gap_stats(df: pd.DataFrame, threshold: float = 0.05, sessions: int = 504) -> dict[str, Any]:
    d = df.iloc[-sessions:]
    gaps = (d["Open"] / d["Close"].shift(1) - 1).dropna()
    big = gaps[gaps.abs() > threshold]
    return {"gaps_over_threshold": len(big), "threshold": threshold, "sessions": len(gaps),
            "largest_gap": float(gaps.loc[gaps.abs().idxmax()]) if len(gaps) else None,
            "largest_gap_date": gaps.abs().idxmax().date().isoformat() if len(gaps) else None}


def compute_risk(px: pd.DataFrame, bench: pd.DataFrame | None) -> dict[str, Any]:
    s = _adj(px)
    out: dict[str, Any] = {"data_through": px.index[-1].date().isoformat()}
    out["vol_1y"] = annualised_vol(s, 252)
    out["vol_3y"] = annualised_vol(s, 756)
    for label, n in (("1y", 252), ("3y", 756), ("5y", 1260)):
        mdd, pk, tr = max_drawdown(s.iloc[-n:])
        out[f"max_drawdown_{label}"] = mdd
        out[f"max_drawdown_{label}_window"] = [pk.date().isoformat() if pk is not None else None,
                                                tr.date().isoformat() if tr is not None else None]
    out["var_95_1d"], out["cvar_95_1d"] = historical_var(s)
    out["gaps"] = gap_stats(px)
    vol = px["Volume"].astype(float)
    out["avg_dollar_volume_20d"] = float((vol.iloc[-20:] * px["Close"].iloc[-20:]).mean())
    if bench is not None and not bench.empty:
        b = _adj(bench)
        out["beta_2y"], out["beta_obs"] = beta(s, b, 504)
        out["beta_1y"], _ = beta(s, b, 252)
        out["upside_capture"], out["downside_capture"], out["capture_months"] = capture_ratios(s.iloc[-1260:], b.iloc[-1260:])
        joined = pd.concat([s.pct_change(), b.pct_change()], axis=1, join="inner").dropna().iloc[-252:]
        out["correlation_1y"] = float(joined.iloc[:, 0].corr(joined.iloc[:, 1])) if len(joined) > 60 else None
        out["benchmark_vol_1y"] = annualised_vol(b, 252)
    else:
        out["beta_2y"] = out["beta_1y"] = out["upside_capture"] = out["downside_capture"] = None
    return out


def sharpe_like(returns: pd.Series, rf: float = 0.0) -> float | None:
    r = returns.dropna()
    if len(r) < 60 or r.std() == 0:
        return None
    return float((r.mean() * 252 - rf) / (r.std() * math.sqrt(252)))
