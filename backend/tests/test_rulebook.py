"""The team rulebook: measures, pillars, labels, gates, fair value, sizing and confidence."""

from datetime import date, timedelta

import pytest

from backend.analysis import decision as D
from backend.analysis import factors as F


def raw(ticker="AAA", sector="Information Technology", price=100.0, fair=120.0, gates=None, **meas):
    base = {"roic_5y": 0.2, "roic_stability": 0.02, "fcf_conversion": 1.0, "fcf_yield": 0.05, "growth_gap": 0.02,
            "pe_vs_history": 0.3, "mom_12_1": 0.2, "trend_200": 0.05, "_slope_200": 0.01, "eps_rev_90d": 0.03,
            "surprise_4q": 0.04}
    base.update(meas)
    base = {k: v for k, v in base.items() if v is not None}
    return {"ticker": ticker, "sector": sector, "measures": base, "missing": {}, "detail": {}, "gates": gates or [],
            "price": price, "fair_value": fair}


def pil(q, v, m, r, sparse=()):
    return {k: {"score": s, "weight": 0.25, "measures": [], "sparse": k in sparse, "missing_ratio": 0.0}
            for k, s in (("quality", q), ("value", v), ("momentum", m), ("revisions", r))}


# ------------------------------------------------------------------ labels (first matching rule wins)
@pytest.mark.parametrize("scores,price,fair,label", [
    ((90, 85, 85, 85), 100, 110, "Strong Buy"),
    ((90, 85, 85, 85), 103, 100, "Buy"),            # price above fair value => not Strong Buy, within 5% => Buy
    ((80, 80, 75, 70), 100, 100, "Buy"),
    ((80, 30, 60, 60), 100, 120, "Wait"),           # quality but value pillar < 50
    ((80, 60, 30, 60), 100, 120, "Wait"),           # quality but momentum < 40
    ((60, 60, 60, 60), 100, 120, "Watchlist"),
    ((20, 90, 90, 90), 100, 120, "Reject"),         # quality < 25
    ((35, 35, 35, 35), 100, 120, "Reject"),         # composite < 40
])
def test_label_rules(scores, price, fair, label):
    out = F.classify(raw(price=price, fair=fair), pil(*scores))
    assert out["label"] == label, out["trace"]


def test_buy_needs_no_pillar_below_25():
    out = F.classify(raw(price=90, fair=100), pil(95, 95, 95, 20))
    assert out["label"] != "Buy" and out["label"] != "Strong Buy"


def test_gates_only_lower_the_label():
    g = [{"id": "dilution", "effect": "Watchlist", "reason": "share count rising"}]
    assert F.classify(raw(price=90, fair=100, gates=g), pil(90, 90, 90, 90))["label"] == "Watchlist"
    # a gate never raises a Reject
    assert F.classify(raw(gates=g), pil(10, 10, 10, 10))["label"] == "Reject"
    no_edge = [{"id": "no_market_edge", "effect": "Buy", "reason": "no credible difference"}]
    assert F.classify(raw(price=90, fair=100), pil(90, 90, 90, 90), no_edge)["label"] == "Buy"


def test_two_sparse_pillars_is_insufficient():
    out = F.classify(raw(), pil(50, 50, 80, 80, sparse=("quality", "value")))
    assert out["label"] == F.INSUFFICIENT


# ------------------------------------------------------------------ pillars and percentiles
def test_sector_percentile_and_fallbacks():
    peers = [raw(ticker=f"P{i}", roic_5y=0.01 * i) for i in range(10)]
    me = raw(roic_5y=0.095)
    s, basis, n = F.score_measure("roic_5y", 0.095, me["sector"], peers + [me], "AAA")
    assert basis == "sector percentile" and n == 10 and s == pytest.approx(100.0)
    s2, basis2, _ = F.score_measure("roic_5y", 0.165, me["sector"], None, "AAA")
    assert basis2.startswith("absolute anchors") and s2 == pytest.approx(50.0)
    # lower-is-better measure
    s3, _, _ = F.score_measure("roic_stability", 0.0, me["sector"], peers + [me], "AAA")
    assert s3 == pytest.approx(100.0)


def test_pillar_with_more_than_half_missing_counts_50():
    r = raw(eps_rev_90d=None, surprise_4q=None)
    r["missing"] = {"eps_rev_90d": "no consensus", "surprise_4q": "no history"}
    p = F.pillars(r, None)
    assert p["revisions"]["score"] == 50.0 and p["revisions"]["sparse"]


def test_not_applicable_measures_do_not_count_as_missing():
    r = raw(fcf_conversion=None, growth_gap=None)
    r["missing"] = {"fcf_conversion": "not applicable to financials", "growth_gap": "not applicable to financials"}
    p = F.pillars(r, None)
    assert not p["quality"]["sparse"] and not p["value"]["sparse"]


# ------------------------------------------------------------------ valuation maths
def test_dcf_and_reverse_dcf_are_inverse():
    v = F.dcf_value(100.0, 0.08, 0.09, 0.025, 10)
    assert F.implied_growth(v, 100.0, 0.09, 0.025, 10) == pytest.approx(0.08, abs=1e-6)
    assert F.implied_growth(1000.0, -5.0, 0.09, 0.025, 10) is None
    # zero growth: PV of a flat perpetuity-like stream, sanity bound
    # fading growth is worth less than constant growth, more than no growth
    assert F.dcf_value(1.0, 0.0, 0.09, 0.025, 10) < F.dcf_value(1.0, 0.15, 0.09, 0.025, 10, 5) < F.dcf_value(1.0, 0.15, 0.09, 0.025, 10)
    # with growth equal to terminal growth the fade changes nothing
    assert F.dcf_value(1.0, 0.025, 0.09, 0.025, 10, 5) == pytest.approx(F.dcf_value(1.0, 0.025, 0.09, 0.025, 10))
    # 10 flat years (6.418) + Gordon terminal 1.025/0.065 discounted 10 years (6.661)
    assert F.dcf_value(1.0, 0.0, 0.09, 0.025, 10) == pytest.approx(13.079, abs=0.001)


def test_buy_below_margin_of_safety():
    assert F.buy_below(100.0, 80) == 85.0
    assert F.buy_below(100.0, 95) == 90.0
    assert F.buy_below(None, 95) is None


def test_position_size_and_halving():
    assert F.position_size("Strong Buy", 0.2, 0.18, -0.2)["size"] == 0.05
    assert F.position_size("Buy", 0.2, 0.18, -0.2)["size"] == 0.03
    assert F.position_size("Buy", 0.4, 0.18, -0.2)["size"] == 0.015          # volatility > 1.5× market
    assert F.position_size("Strong Buy", 0.4, 0.18, -0.5)["size"] == 0.0125  # both halvings
    assert F.position_size("Wait", 0.2, 0.18, -0.2)["size"] == 0.0


def test_confidence_rules():
    c, why = F.confidence("good", pil(60, 60, 60, 60), llm_used=True)
    assert c == 0.85
    c2, _ = F.confidence("acceptable", pil(85, 15, 60, 60, sparse=("value",)), llm_used=False)
    assert c2 == pytest.approx(0.85 - 0.08 - 0.10 - 0.10 - 0.10)
    c3, _ = F.confidence("poor", pil(90, 10, 50, 50, sparse=("value", "revisions", "momentum")), llm_used=False)
    assert c3 == pytest.approx(0.15)  # 0.85 − 0.20 − 3×0.10 − 0.10 − 0.10
    c4, _ = F.confidence("poor", pil(90, 10, 50, 50, sparse=("value", "revisions", "momentum", "quality")), llm_used=False)
    assert c4 == 0.10  # floor


# ------------------------------------------------------------------ measures from SEC facts
def _fact(val, end, start=None, form="10-K", filed=None, unit="USD"):
    e = {"val": val, "end": end.isoformat(), "form": form, "filed": (filed or end + timedelta(days=50)).isoformat()}
    if start:
        e["start"] = start.isoformat()
    return e


def facts(years=5, oi=20.0, assets=200.0, cl=40.0, cash=20.0, ni=15.0, ocf=22.0, capex=4.0, sbc=1.0, rev0=100.0, g=0.1,
          shares=(10.0, 10.0, 10.0)):
    g_ = {}

    def add(c, e, unit="USD"):
        g_.setdefault(c, {"units": {}})["units"].setdefault(unit, []).append(e)

    for i in range(years):
        end = date(2021 + i, 12, 31)
        st = date(2021 + i, 1, 1)
        add("OperatingIncomeLoss", _fact(oi, end, st))
        add("NetIncomeLoss", _fact(ni, end, st))
        add("NetCashProvidedByUsedInOperatingActivities", _fact(ocf, end, st))
        add("PaymentsToAcquirePropertyPlantAndEquipment", _fact(capex, end, st))
        add("ShareBasedCompensation", _fact(sbc, end, st))
        add("Revenues", _fact(rev0 * (1 + g) ** i, end, st))
        add("IncomeTaxExpenseBenefit", _fact(oi * 0.2, end, st))
        add("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", _fact(oi, end, st))
        add("Assets", _fact(assets, end))
        add("LiabilitiesCurrent", _fact(cl, end))
        add("CashAndCashEquivalentsAtCarryingValue", _fact(cash, end))
        add("EarningsPerShareDiluted", _fact(1.5, end, st), "USD/shares")
    for i, sh in enumerate(shares):
        end = date(2021 + years - len(shares) + i, 12, 31)
        add("WeightedAverageNumberOfDilutedSharesOutstanding", _fact(sh, end, date(end.year, 1, 1)), "shares")
    return {"facts": {"us-gaap": g_}}


def test_measures_from_facts_roic_fcf_and_gates():
    info = {"currentPrice": 30.0, "marketCap": 300.0, "totalDebt": 10.0, "totalCash": 20.0, "trailingPE": 20.0,
            "trailingEps": 1.5, "ebitda": 25.0}
    r = F.compute_measures(F.FactorInputs("AAA", "Industrials", info, facts(), as_of=date(2026, 6, 1)))
    m = r["measures"]
    # ROIC = OI × (1 − 20%) / (assets − current liabilities − cash) = 16 / 140
    assert m["roic_5y"] == pytest.approx(16 / 140)
    assert m["roic_stability"] == pytest.approx(0.0)
    assert m["fcf_conversion"] == pytest.approx((22 - 4 - 1) / 15)
    assert m["fcf_yield"] == pytest.approx(18 / 300)
    assert r["detail"]["revenue_cagr"] == pytest.approx(0.1, abs=0.002)
    assert "growth_gap" in m and r["fair_value"] and r["fair_value"] > 0
    assert not r["gates"]


def test_dilution_and_red_flag_gates():
    info = {"currentPrice": 30.0, "marketCap": 300.0, "totalDebt": 10.0, "totalCash": 20.0}
    filings = [{"form": "8-K", "items": "4.02,9.01", "filingDate": "2026-05-01", "url": "u"}]
    r = F.compute_measures(F.FactorInputs("AAA", "Industrials", info, facts(shares=(10.0, 11.0, 12.2)), filings=filings,
                                          as_of=date(2026, 6, 1)))
    ids = {g["id"]: g["effect"] for g in r["gates"]}
    assert ids == {"red_flag_filing": "Reject", "dilution": "Watchlist"}
    listing = [{"form": "8-K", "items": "3.01", "filingDate": "2026-05-01", "url": "u"}]
    r2 = F.compute_measures(F.FactorInputs("AAA", "Industrials", info, facts(), filings=listing, as_of=date(2026, 6, 1)))
    assert {g["id"]: g["effect"] for g in r2["gates"]} == {"listing_notice": "Wait"}


def test_leverage_gate_needs_negative_fcf():
    info = {"currentPrice": 30.0, "marketCap": 300.0, "totalDebt": 500.0, "totalCash": 0.0, "ebitda": 50.0}
    r = F.compute_measures(F.FactorInputs("AAA", "Industrials", info, facts(ocf=2.0, capex=10.0)))
    assert any(g["id"] == "leverage" and g["effect"] == "Reject" for g in r["gates"])
    r2 = F.compute_measures(F.FactorInputs("AAA", "Industrials", info, facts()))
    assert not any(g["id"] == "leverage" for g in r2["gates"])


def test_financials_use_roe_and_earnings_yield():
    f = facts()
    for i in range(5):
        f["facts"]["us-gaap"].setdefault("StockholdersEquity", {"units": {"USD": []}})["units"]["USD"].append(
            _fact(100.0, date(2021 + i, 12, 31)))
    r = F.compute_measures(F.FactorInputs("BNK", "Financials", {"trailingPE": 10.0, "marketCap": 300.0,
                                                                "currentPrice": 30.0}, f))
    assert r["measures"]["roic_5y"] == pytest.approx(0.15)
    assert r["measures"]["fcf_yield"] == pytest.approx(0.1)
    assert r["missing"]["fcf_conversion"].startswith("not applicable")


# ------------------------------------------------------------------ price ladder and sizing in the report
def test_entry_price_and_allocation_follow_rulebook():
    rb = {"price": 90.0, "fair_value": 100.0, "buy_below": 85.0, "fair_value_methods": [{"method": "DCF", "value": 100.0}]}
    e = D.entry_price("Wait", rb, "2026-01-01")
    assert e["available"] and e["buy_below"] == 85.0 and "85.00" in e["text"]
    assert not D.entry_price("Wait", {"price": 90.0}, "2026-01-01")["available"]
    a = D.allocation("Buy", {"size": 0.03, "notes": []}, None)
    assert a["high"] == 0.03
    assert D.allocation("Buy", {"size": 0.05, "notes": []}, {"available": True, "max_position": 0.02})["high"] == 0.02
    assert D.allocation("Reject", {"size": 0.0}, None)["high"] == 0.0


def test_recent_split_and_implausible_pe_are_data_gates():
    import pandas as pd

    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=300)
    daily = pd.DataFrame({"Close": 100.0, "Adj Close": 100.0, "Stock Splits": 0.0}, index=idx)
    daily.loc[idx[-3], "Stock Splits"] = 8.0
    info = {"currentPrice": 30.0, "marketCap": 300.0, "trailingPE": 0.09, "trailingEps": 900.0}
    r = F.compute_measures(F.FactorInputs("AAA", "Industrials", info, facts(), daily=daily, as_of=date(2026, 6, 1)))
    ids = {g["id"]: g["effect"] for g in r["gates"]}
    assert ids["recent_split"] == "Watchlist" and ids["vendor_inconsistency"] == "Watchlist"


def test_dcf_base_is_three_year_average():
    f = facts()
    # make the latest year a peak: OCF 3× normal
    f["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"][-1]["val"] = 66.0
    r = F.compute_measures(F.FactorInputs("AAA", "Industrials", {"currentPrice": 30.0, "marketCap": 300.0}, f))
    d = r["detail"]
    assert d["fcf_after_sbc"] == pytest.approx(66 - 4 - 1)
    assert d["fcf_base"] == pytest.approx(((22 - 5) * 2 + (66 - 5)) / 3)
    assert d["fcf_base_method"].startswith("3-year average")


def test_gates_report_whether_they_bind():
    g = [{"id": "no_market_edge", "effect": "Buy", "reason": "no edge."}]
    out = F.classify(raw(), pil(60, 60, 60, 60), g)          # Watchlist already below Buy
    assert out["label"] == "Watchlist" and out["gates"][0]["binding"] is False
    out2 = F.classify(raw(price=90, fair=100), pil(90, 90, 90, 90), [dict(g[0])])
    assert out2["label"] == "Buy" and out2["gates"][0]["binding"] is True and ".." not in out2["trace"][-1]


def test_financials_get_earnings_based_market_belief():
    f = facts(ni=15.0)
    for i in range(5):
        f["facts"]["us-gaap"].setdefault("StockholdersEquity", {"units": {"USD": []}})["units"]["USD"].append(
            _fact(100.0, date(2021 + i, 12, 31)))
    r = F.compute_measures(F.FactorInputs("BNK", "Financials", {"trailingPE": 10.0, "marketCap": 300.0,
                                                                "currentPrice": 30.0}, f))
    d = r["detail"]
    assert d["achieved_growth_label"] == "net income" and d["implied_growth"] is not None
    assert d["implied_growth"] == pytest.approx(F.implied_growth(300.0, 15.0, 0.09, 0.025, 10))


def test_leverage_limit_is_sector_aware():
    info = {"currentPrice": 30.0, "marketCap": 300.0, "totalDebt": 280.0, "totalCash": 0.0, "ebitda": 50.0}  # 5.6x
    neg = facts(ocf=2.0, capex=10.0)
    assert any(g["id"] == "leverage" for g in F.compute_measures(F.FactorInputs("A", "Industrials", info, neg))["gates"])
    assert not any(g["id"] == "leverage" for g in F.compute_measures(F.FactorInputs("U", "Utilities", info, neg))["gates"])
