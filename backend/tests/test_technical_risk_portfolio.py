import math

import numpy as np
import pandas as pd
import pytest

from backend.tests.fixtures import price_frame
from backend.tools import portfolio_metrics as PM
from backend.tools.risk_metrics import beta, capture_ratios, compute_risk, historical_var
from backend.tools.technical_indicators import (
    atr,
    compute_technicals,
    ema,
    macd,
    max_drawdown,
    rsi,
    sma,
    swing_levels,
)


def test_sma_ema():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert sma(s, 3).tolist()[2:] == [2, 3, 4]
    assert pd.isna(sma(s, 3).iloc[1])
    e = ema(pd.Series([10.0] * 20), 5)
    assert e.iloc[-1] == pytest.approx(10.0)


def test_rsi_bounds_and_monotonic():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert rsi(up).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert rsi(down).iloc[-1] == pytest.approx(0.0, abs=1e-9)
    mixed = pd.Series([44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
                       46.00, 46.03, 46.41, 46.22, 45.64])
    v = rsi(mixed).iloc[-1]
    assert 0 < v < 100


def test_macd_zero_for_flat_series():
    line, sig, hist = macd(pd.Series([50.0] * 60))
    assert line.iloc[-1] == pytest.approx(0) and hist.iloc[-1] == pytest.approx(0)


def test_atr_constant_range():
    n = 30
    h = pd.Series([11.0] * n)
    lo = pd.Series([9.0] * n)
    c = pd.Series([10.0] * n)
    assert atr(h, lo, c).iloc[-1] == pytest.approx(2.0)


def test_max_drawdown():
    s = pd.Series([100, 120, 60, 90, 130], index=pd.date_range("2024-01-01", periods=5))
    dd, pk, tr = max_drawdown(s)
    assert dd == pytest.approx(-0.5)
    assert str(pk.date()) == "2024-01-02" and str(tr.date()) == "2024-01-03"


def test_compute_technicals_and_levels():
    px = price_frame(3)
    bench = price_frame(4)
    t = compute_technicals(px, bench)
    assert t["sma_200"] is not None and 0 <= t["rsi_14"] <= 100
    assert t["trend_long"] in ("up", "down", "sideways")
    assert set(t["rs_benchmark"]) == {"1m", "3m", "6m", "12m"}
    for z in swing_levels(px):
        assert z["low"] <= z["high"] and z["type"] in ("support", "resistance")


def test_short_history_gives_none_not_guesses():
    px = price_frame(5, n=60)
    t = compute_technicals(px, None)
    assert t["sma_200"] is None and t["trend_long"] == "insufficient data"
    assert t["rs_benchmark"] is None


def test_beta_of_levered_series():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 600)
    idx = pd.bdate_range("2022-01-03", periods=600)
    b = pd.Series(100 * np.cumprod(1 + r), index=idx)
    s = pd.Series(100 * np.cumprod(1 + 2 * r), index=idx)
    val, n = beta(s, b, 504)
    assert val == pytest.approx(2.0, rel=1e-6) and n == 504


def test_capture_ratios_identical_series():
    px = price_frame(7, n=800)["Adj Close"]
    up, down, months = capture_ratios(px, px)
    assert up == pytest.approx(1.0) and down == pytest.approx(1.0) and months >= 12


def test_var_and_risk_bundle():
    px = price_frame(8)
    q, cvar = historical_var(px["Adj Close"])
    assert cvar <= q < 0
    r = compute_risk(px, price_frame(9))
    assert r["beta_2y"] is not None and r["max_drawdown_3y"] <= 0


def test_weights_from_quantity_and_percent_weights():
    w = PM.weights_from_holdings([{"ticker": "A", "quantity": 10}, {"ticker": "B", "market_value": 1000}],
                                 {"A": 100.0}, cash=1000)
    assert w["weights"]["A"] == pytest.approx(1000 / 3000) and w["cash_weight"] == pytest.approx(1 / 3)
    w2 = PM.weights_from_holdings([{"ticker": "A", "weight": 60}, {"ticker": "B", "weight": 30}], {}, 0)
    assert w2["weights"]["A"] == pytest.approx(0.6) and w2["cash_weight"] == pytest.approx(0.1)
    w3 = PM.weights_from_holdings([{"ticker": "A", "quantity": 5}], {}, 0)
    assert w3["missing_prices"] == ["A"]


def test_portfolio_vol_and_candidate_impact():
    pm = {t: price_frame(i) for i, t in enumerate(["A", "B", "C"], start=20)}
    rets = PM.returns_frame(pm)
    v = PM.portfolio_vol({"A": 0.5, "B": 0.5}, rets)
    va = PM.portfolio_vol({"A": 1.0}, rets)
    assert v < va + 1e-9  # diversification with independent series
    imp = PM.candidate_impact({"A": 0.5, "B": 0.5}, 0.0, "C", 0.1, rets, {"A": "Tech", "B": "Tech", "C": "Health"})
    assert imp["hhi_after"] < imp["hhi_before"]
    assert imp["sector_after"]["Health"] == pytest.approx(0.1)
    assert abs(sum(PM.risk_contributions({"A": 0.5, "B": 0.5}, rets).values()) - 1) < 1e-9
    assert not math.isnan(imp["vol_after"])


def test_scenarios_and_negative_eps():
    from backend.tools.scenario_analysis import eps_scenarios, market_stress

    s = eps_scenarios(100, 5, 6, 0.1, 15, 20, 25, 20)
    assert s["available"] and s["scenarios"]["bear"]["price"] < s["scenarios"]["base"]["price"] < s["scenarios"]["bull"]["price"]
    assert not eps_scenarios(100, -1, None, None, 15, 20, 25, None)["available"]
    assert market_stress(1.5)[1]["estimated_move"] == pytest.approx(-0.30)
