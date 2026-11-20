"""Deterministic technical indicators on daily bars.

Conventions: Wilder smoothing for RSI(14) and ATR(14); MACD(12, 26, 9) on EMAs;
relative strength = stock total return − benchmark total return over a window,
using adjusted closes. Support/resistance: swing highs/lows (local extrema within
±10 sessions) over the last 252 sessions, clustered when within 1×ATR; zones are
reported as estimates with their touch counts.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & avg_gain.notna()] = 100.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def annualised_vol(close: pd.Series, window: int | None = None) -> float | None:
    r = np.log(close / close.shift(1)).dropna()
    if window:
        r = r.iloc[-window:]
    if len(r) < 10:
        return None
    return float(r.std(ddof=1) * math.sqrt(252))


def max_drawdown(close: pd.Series) -> tuple[float | None, Any, Any]:
    if close is None or len(close) < 2:
        return None, None, None
    peak = close.cummax()
    dd = close / peak - 1
    trough = dd.idxmin()
    peak_date = close.loc[:trough].idxmax()
    return float(dd.min()), peak_date, trough


def total_return(close: pd.Series, days: int) -> float | None:
    if close is None or len(close) <= days:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - days] - 1)


def swing_levels(df: pd.DataFrame, lookback: int = 252, window: int = 10) -> list[dict[str, Any]]:
    d = df.iloc[-lookback:]
    if len(d) < 2 * window + 5:
        return []
    highs, lows = d["High"], d["Low"]
    pts = []
    for i in range(window, len(d) - window):
        h, lo = highs.iloc[i], lows.iloc[i]
        if h == highs.iloc[i - window:i + window + 1].max():
            pts.append((float(h), "high", d.index[i]))
        if lo == lows.iloc[i - window:i + window + 1].min():
            pts.append((float(lo), "low", d.index[i]))
    a = atr(df["High"], df["Low"], df["Close"]).iloc[-1]
    tol = float(a) if a == a else float(d["Close"].iloc[-1]) * 0.02
    pts.sort(key=lambda x: x[0])
    clusters: list[list[tuple]] = []
    for p in pts:
        if clusters and p[0] - clusters[-1][-1][0] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = float(d["Close"].iloc[-1])
    zones = []
    for c in clusters:
        lo, hi = min(x[0] for x in c), max(x[0] for x in c)
        mid = (lo + hi) / 2
        zones.append({"low": round(lo, 2), "high": round(hi, 2), "touches": len(c),
                      "type": "support" if mid < last else "resistance",
                      "last_touch": max(x[2] for x in c).date().isoformat(),
                      "distance_pct": round((mid / last - 1) * 100, 2)})
    return zones


def compute_technicals(px: pd.DataFrame, bench: pd.DataFrame | None, sector: pd.DataFrame | None = None) -> dict[str, Any]:
    """Return a dict of indicator values with dates. Missing data yields None, never guesses."""
    close = px["Close"]
    adj = px["Adj Close"] if "Adj Close" in px else close
    out: dict[str, Any] = {"data_through": px.index[-1].date().isoformat(), "sessions": len(px), "price": float(close.iloc[-1])}
    for n in (20, 50, 100, 200):
        v = sma(close, n).iloc[-1]
        out[f"sma_{n}"] = None if pd.isna(v) else float(v)
        out[f"dist_sma_{n}"] = None if pd.isna(v) else float(close.iloc[-1] / v - 1)
        prev = sma(close, n).iloc[-21] if len(close) > n + 21 else np.nan
        out[f"sma_{n}_slope_1m"] = None if (pd.isna(prev) or pd.isna(v)) else float(v / prev - 1)
    r = rsi(close)
    out["rsi_14"] = None if pd.isna(r.iloc[-1]) else float(r.iloc[-1])
    out["rsi_14_5d_ago"] = None if len(r) < 6 or pd.isna(r.iloc[-6]) else float(r.iloc[-6])
    m_line, m_sig, m_hist = macd(close)
    out["macd"] = None if pd.isna(m_line.iloc[-1]) else float(m_line.iloc[-1])
    out["macd_signal"] = None if pd.isna(m_sig.iloc[-1]) else float(m_sig.iloc[-1])
    out["macd_hist"] = None if pd.isna(m_hist.iloc[-1]) else float(m_hist.iloc[-1])
    out["macd_hist_5d_ago"] = None if len(m_hist) < 6 or pd.isna(m_hist.iloc[-6]) else float(m_hist.iloc[-6])
    a = atr(px["High"], px["Low"], close)
    out["atr_14"] = None if pd.isna(a.iloc[-1]) else float(a.iloc[-1])
    out["atr_pct"] = None if out["atr_14"] is None else out["atr_14"] / float(close.iloc[-1])
    out["vol_20d"] = annualised_vol(adj, 20)
    out["vol_60d"] = annualised_vol(adj, 60)
    out["vol_1y"] = annualised_vol(adj, 252)
    hi52 = float(close.iloc[-252:].max())
    lo52 = float(close.iloc[-252:].min())
    out["high_52w"], out["low_52w"] = hi52, lo52
    out["drawdown_from_52w_high"] = float(close.iloc[-1] / hi52 - 1)
    mdd, pk, tr = max_drawdown(adj.iloc[-252:])
    out["max_drawdown_1y"] = mdd
    out["max_drawdown_1y_dates"] = [str(pk.date()) if pk is not None else None, str(tr.date()) if tr is not None else None]
    vol = px["Volume"].astype(float)
    v20, v50 = vol.iloc[-20:].mean(), vol.iloc[-50:].mean()
    out["volume_20d_avg"], out["volume_50d_avg"] = float(v20), float(v50)
    out["volume_trend"] = float(v20 / v50 - 1) if v50 else None
    out["avg_dollar_volume_20d"] = float((vol.iloc[-20:] * close.iloc[-20:]).mean())
    avg50 = vol.rolling(50, min_periods=50).mean()
    unusual = px.index[-20:][(vol.iloc[-20:] > 2 * avg50.iloc[-20:]).fillna(False).values]
    out["unusual_volume_days_20d"] = [d.date().isoformat() for d in unusual]
    # returns and relative strength
    windows = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
    out["returns"] = {k: total_return(adj, n) for k, n in windows.items()}
    for name, frame in (("benchmark", bench), ("sector", sector)):
        if frame is None or frame.empty:
            out[f"rs_{name}"] = None
            continue
        b = frame["Adj Close"] if "Adj Close" in frame else frame["Close"]
        b = b.reindex(adj.index).ffill()
        out[f"{name}_returns"] = {k: total_return(b, n) for k, n in windows.items()}
        out[f"rs_{name}"] = {k: (None if out["returns"][k] is None or out[f"{name}_returns"][k] is None
                                 else out["returns"][k] - out[f"{name}_returns"][k]) for k in windows}
    # breakout detection (documented): close above prior 50-session closing high
    prior_high = close.iloc[-51:-1].max() if len(close) > 51 else np.nan
    out["prior_50d_high"] = None if pd.isna(prior_high) else float(prior_high)
    if not pd.isna(prior_high) and close.iloc[-1] > prior_high:
        closes_above = int((close.iloc[-3:] > prior_high).sum())
        vol_ok = bool(v50 and vol.iloc[-1] >= 1.5 * v50)
        out["breakout"] = "confirmed" if (closes_above >= 3 and vol_ok) else "possible"
    else:
        out["breakout"] = "none"
    # trend classification
    def trend(dist, slope):
        if dist is None:
            return "insufficient data"
        if dist > 0 and (slope is None or slope > 0):
            return "up"
        if dist < 0 and (slope is None or slope < 0):
            return "down"
        return "sideways"

    out["trend_short"] = trend(out["dist_sma_20"], out["sma_20_slope_1m"])
    out["trend_medium"] = trend(out["dist_sma_50"], out["sma_50_slope_1m"])
    out["trend_long"] = trend(out["dist_sma_200"], out["sma_200_slope_1m"])
    out["golden_cross_state"] = (None if out["sma_50"] is None or out["sma_200"] is None
                                 else ("50 above 200" if out["sma_50"] > out["sma_200"] else "50 below 200"))
    out["levels"] = swing_levels(px)
    return out


def chart_series(px: pd.DataFrame, bench: pd.DataFrame | None, sessions: int = 756) -> dict[str, Any]:
    """Compact series for the frontend (dates, OHLCV, MAs, RSI, MACD, relative performance)."""
    d = px.iloc[-sessions:]
    close = px["Close"]
    ma = {n: sma(close, n).iloc[-sessions:] for n in (20, 50, 200)}
    r = rsi(close).iloc[-sessions:]
    ml, ms, mh = (x.iloc[-sessions:] for x in macd(close))

    def clean(s):
        return [None if pd.isna(v) else round(float(v), 4) for v in s]

    out = {"dates": [i.date().isoformat() for i in d.index], "open": clean(d["Open"]), "high": clean(d["High"]),
           "low": clean(d["Low"]), "close": clean(d["Close"]), "volume": [int(v) if v == v else None for v in d["Volume"]],
           "sma20": clean(ma[20]), "sma50": clean(ma[50]), "sma200": clean(ma[200]), "rsi": clean(r),
           "macd": clean(ml), "macd_signal": clean(ms), "macd_hist": clean(mh)}
    if bench is not None and not bench.empty:
        a = (px["Adj Close"] if "Adj Close" in px else close).iloc[-sessions:]
        b = (bench["Adj Close"] if "Adj Close" in bench else bench["Close"]).reindex(a.index).ffill()
        out["rel_stock"] = clean(a / a.iloc[0] * 100)
        out["rel_bench"] = clean(b / b.dropna().iloc[0] * 100) if b.notna().any() else []
    return out
